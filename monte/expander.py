from ometa.grammar import TreeTransformerGrammar
from ometa.runtime import TreeTransformerBase, ParseError
from terml.nodes import Tag, Term, termMaker as t
### XXX TODO: Create TemporaryExprs for variables generated by
### expansion. Replace all temps with nouns in a single pass at the
### end.

TRUE = t.NounExpr('true')   #Term(Tag('true'), None, None, None)
FALSE = t.NounExpr('false') #Term(Tag('false'), None, None, None)

class ScopeSet(object):
    def __init__(self, bits=()):
        self.contents = list(bits)

    def __sub__(self, other):
        bits = self.contents[:]
        for bit in other:
            if bit in bits:
                bits[bits.index(bit)] = bits[-1]
                del bits[-1]
        return ScopeSet(bits)

    def __and__(self, other):
        if len(self) > len(other):
            big = self.contents
            small = list(other)
        else:
            small = self.contents
            big = list(other)
        bits = []
        for bit in small:
            if bit in big:
                bits.append(bit)
        return ScopeSet(bits)

    def __or__(self, other):
        bits = list(other)
        for bit in self.contents:
            if bit not in bits:
                bits.append(bit)
        return ScopeSet(bits)

    def getKeys(self):
        return self.contents[:]

    def __contains__(self, o):
        return o in self.contents

    def __iter__(self):
        return iter(self.contents)

    def __len__(self):
        return len(self.contents)

    def butNot(self, other):
        bits = list(other)
        return ScopeSet(x for x in self.contents if x not in bits)

class StaticScope(object):
    def __init__(self, namesRead=None, namesSet=None, metaStateExprFlag=False,
                 defNames=None, varNames=None):
        if namesRead is None:
            namesRead = ScopeSet()
        if namesSet is None:
            namesSet = ScopeSet()
        if defNames is None:
            defNames = ScopeSet()
        if varNames is None:
            varNames = ScopeSet()
        self.namesRead = ScopeSet(namesRead)
        self.namesSet = ScopeSet(namesSet)
        self.defNames = ScopeSet(defNames)
        self.varNames = ScopeSet(varNames)
        self.metaStateExprFlag = metaStateExprFlag

    def hide(self):
        return StaticScope(self.namesRead, self.namesSet,
                           self.metaStateExprFlag,
                           None, None)
    def add(self, right):
        """
        For processing normal expressions left to right, where all definitions
        are exported, but uses are hidden by definitions to their left.
        """
        if right is None:
            return self
        rightNamesRead = (right.namesRead - self.defNames) - self.varNames
        rightNamesSet = (right.namesSet - self.varNames)
        badAssigns = rightNamesSet & self.defNames
        if badAssigns:
            if len(badAssigns) == 1:
                raise ValueError("Can't assign to final noun %r" % tuple(badAssigns))
            else:
                raise ValueError("Can't assign to final nouns %s" % ', '.join(badAssigns))
            #rightNamesSet = rightNamesSet - badAssigns

        return StaticScope(self.namesRead | rightNamesRead,
                           self.namesSet | rightNamesSet,
                           self.metaStateExprFlag or right.metaStateExprFlag,
                           self.defNames | right.defNames,
                           self.varNames | right.varNames)


    def namesUsed(self):
        """
        What are the names of variables used by this expression that refer to
        variables defined outside this expression?

        Union of namesRead and namesSet.
        """
        return self.namesRead | self.namesSet

    def outNames(self):
        """
        What variables are defined in this expression that are visible after
        this expression (i.e., to its right)?

        Union of defNames and varNames.
        """

        return self.defNames | self.varNames

    def __repr__(self):
        return "<%r := %r =~ %r + var %r %s>" % (list(self.namesSet),
                                                 list(self.namesRead),
                                                 list(self.defNames),
                                                 list(self.varNames),
                                                 ("meta.getState()"
                                                  if self.metaStateExprFlag
                                                  else ""))

def getExports(scope, used):
    outs = scope.outNames()
    if used is not None and not used.metaStateExprFlag:
        outs = outs & used.namesUsed()
    return outs

def union(scopes, result=StaticScope()):
    for sc in scopes:
        result = result.add(sc)
    return result

def foldr(f, a, bs):
    for b in bs:
        a = f(a, b)
    return a

def verbAssignError(parser, target):
    name = target.tag.name
    if name == "QuasiLiteralExpr":
        err("Can't use update-assign syntax on a \"$\"-hole. "
            "Use explicit \":=\" syntax instead.", parser)
    elif name == "QuasiPatternExpr":
        err("Can't use update-assign syntax on a \"@\"-hole. "
            "Use explicit \":=\" syntax instead.", parser)
    else:
        err("Can only update-assign nouns and calls", parser)

def err(msg, parser):
    raise parser.input.error.withMessage([("message", msg)])

def expandCallVerbAssign(self, verb, args, receiver, methVerb, methArgs):
    r = self.mktemp("recip")
    prelude = t.Def(t.FinalPattern(r, None), None, receiver)
    seq = [prelude]
    setArgs = []
    for arg in methArgs.args:
        a = self.mktemp("arg")
        seq.append(t.Def(t.FinalPattern(a, None), None, arg))
        setArgs.append(a)
    seq.extend(self.apply("transform", [t.Assign(t.MethodCallExpr(r, methVerb, setArgs), t.MethodCallExpr(t.MethodCallExpr(r, methVerb, setArgs), verb, args))])[0][0].args[0].args)
    return t.SeqExpr(seq)


def expandDef(self, patt, optEj, rval, nouns):
    pattScope = scope(patt)
    defPatts = pattScope.defNames
    varPatts = pattScope.varNames
    rvalScope = scope(rval)
    if optEj:
        rvalScope = scope(optEj).add(rvalScope)
    rvalUsed = rvalScope.namesUsed()
    if len(varPatts & rvalUsed) != 0:
                err("Circular 'var' definition not allowed", self)
    if len(pattScope.namesUsed() & rvalScope.outNames()) != 0:
                err("Pattern may not use var defined on the right", self)
    conflicts = defPatts & rvalUsed
    if len(conflicts) == 0:
        return t.Def(patt, optEj, rval)
    else:
        promises = []
        resolves = []
        renamings = {}
        for oldNameStr in conflicts.getKeys():
            newName = self.mktemp(oldNameStr)
            newNameR = self.mktemp(oldNameStr + "R")
            renamings[oldNameStr] = newName
             # def [newName, newNameR] := Ref.promise()
            pair = [t.FinalPattern(newName, None),
                    t.FinalPattern(newNameR, None)]
            promises.append(t.Def(t.ListPattern(pair, None), None,
                                  mcall("Ref", "promise")))
            resolves.append(t.MethodCallExpr(newNameR, "resolve",
                                            [t.NounExpr(oldNameStr)]))
        resName = self.mktemp("value")
        resolves.append(resName)
        cr = CycleRenamer([rval])
        cr.renamings = renamings
        rval =  cr.apply("transform")[0]
        resPatt = t.FinalPattern(resName, None)
        resDef = t.Def(resPatt, None, t.Def(patt, optEj, rval))
        return t.SeqExpr(promises + [resDef] + resolves)

computeStaticScopeRules = """
null = anything:t ?(t is None or t.tag.name == 'null') -> StaticScope()
LiteralExpr(:val) -> StaticScope()
NounExpr(@name) -> StaticScope(namesRead=[name])
TempNounExpr(@name @idx) -> StaticScope(namesRead=[name + str(idx)])
SlotExpr(@name) -> StaticScope(namesRead=[name])
BindingExpr(@b) -> b
HideExpr(@blockScope) -> blockScope.hide()
Meta("Context") -> StaticScope()
Meta("State") -> StaticScope(metaStateExprFlag=True)
SeqExpr(@scopes) -> union(scopes)
MethodCallExpr(@receiverScope :verb @argScopes) -> union(argScopes, receiverScope)

Def(@patternScope @exitScope @exprScope) -> patternScope.add(exitScope).add(exprScope)
Assign(NounExpr(@name) @rightScope) -> StaticScope(namesSet=[name]).add(rightScope)
Assign(TempNounExpr(@name @idx) @rightScope) -> StaticScope(namesSet=[name + str(idx)]).add(rightScope)

IgnorePattern(@guardScope) -> guardScope or StaticScope()
VarPattern(NounExpr(@name) @guardScope) -> StaticScope(varNames=[name]).add(guardScope)
VarPattern(TempNounExpr(@name @idx) @guardScope) -> StaticScope(varNames=[name + str(idx)]).add(guardScope)
FinalPattern(NounExpr(@name) @guardScope) -> StaticScope(defNames=[name]).add(guardScope)
FinalPattern(TempNounExpr(@name @idx) @guardScope) -> StaticScope(defNames=[name + str(idx)]).add(guardScope)
SlotPattern(NounExpr(@name) @guardScope) -> StaticScope(varNames=[name]).add(guardScope)
BindingPattern(NounExpr(@name)) -> StaticScope(varNames=[name])
BindingPattern(TempNounExpr(@name @idx)) -> StaticScope(varNames=[name + str(idx)])
ListPattern(@patternScopes null) -> union(patternScopes)
ViaPattern(@exprScope @patternScope) -> exprScope.add(patternScope)

Script(@extends @methodScopes @matcherScopes) -> union(methodScopes + matcherScopes)
Object(@doco @nameScope @auditorScope @scriptScope) -> nameScope.add(union(auditorScope).add(scriptScope))
Method(@doco @verb @paramsScope @guardScope @blockScope) -> union(paramsScope + [guardScope, blockScope.hide()]).hide()
Matcher(@patternScope @blockScope) -> patternScope.add(blockScope).hide()

If(@testScope @consqScope @altScope) -> testScope.add(consqScope).hide().add(altScope).hide()
KernelTry(@tryScope @patternScope @catchScope) -> tryScope.hide().add(patternScope.add(catchScope)).hide()
Finally(@tryScope @finallyScope) -> tryScope.hide().add(finallyScope).hide()
Escape(@ejScope @bodyScope Catch(@argScope @catcherScope)) -> ejScope.add(bodyScope).hide().add(argScope.add(catcherScope)).hide()
Escape(@ejScope @bodyScope null) -> ejScope.add(bodyScope).hide()

MatchBind(@specimen @pattern) -> specimen.add(pattern)

LogicalAnd(delayed:left  delayed:right) -> self.expandAndScope(left).add(self.expandAndScope(right))
LogicalAnd(delayed:left  @rightScope) -> self.expandAndScope(left).add(right)
LogicalAnd(@leftScope delayed:right) -> leftScope.add(self.expandAndScope(right))
LogicalAnd(@leftScope @rightScope) -> leftScope.add(rightScope)

LogicalOr(expand:left expand:right) -> StaticScope(left.namesRead | right.namesRead,
                                                   left.namesSet | right.namesSet,
                                                   left.metaStateExprFlag or right.metaStateExprFlag,
                                                   left.defNames | right.defNames,
                                                   left.varNames | right.varNames)
"""

def scope(term):
    x = StaticScopeTransformer.transform(term)[0]
    if x is None:
        return StaticScope()
    return x

def mcall(noun, verb, *expr):
    return t.MethodCallExpr(t.NounExpr(noun), verb, expr)

def putVerb(verb):
    if verb == "get":
        return "put"
    elif verb == "run":
        return "setRun"
    elif verb.startswith("get"):
        return "set"+verb[3:]
    elif verb.startswith("__get"):
        return "__set"+verb[5:]

def buildQuasi(pairs):
    textParts = []
    exprParts = []
    patternParts = []
    for text, expr, patt in pairs:
        if expr:
            textParts.append("${%s}" % (len(exprParts),))
            exprParts.append(expr)
        elif patt:
            textParts.append("@{%s}" % (len(patternParts),))
            patternParts.append(patt)
        else:
            textParts.append(text.data)
    return t.LiteralExpr(''.join(textParts)), exprParts, patternParts

#implicit rules:
# data transforms to itself
# tuples transform to tuples with each item transformed
# other terms transform to terms of the same name with each arg transformed
expander = """

#no args and lowercase means this isn't automatically treated as a
#term, so we explicitly deal with it here
null = anything:t ?(t is None or t.tag.name == 'null')

nameAndString = NounExpr(:name):e !(self.nouns.add(name)) -> e, name.data
     | SlotExpr(NounExpr(:name)):e -> e, '&' + name.data
     | BindingExpr(NounExpr(:name)):e -> e, '&&' + name.data

     | VarPattern(name:name :guard):p transform(p):e -> e, name
     | BindPattern(name:name :guard):p transform(p):e -> e, name
     | FinalPattern(name:name :guard):p transform(p):e -> e, name
     | SlotPattern(name:name :guard):p transform(p):e -> e, '&' + name
     | BindingPattern(name:name):p transform(p):e -> e, '&&' + name

name = NounExpr(:name) !(self.nouns.add(name)) -> name.data
     | SlotExpr(:name) -> '&' + name.data
     | BindingExpr(NounExpr(:name)) -> '&&' + name.data


NounExpr(@name) !(self.nouns.add(name)) -> t.NounExpr(name)
URIExpr(@scheme @body) -> mcall(scheme + "__uriGetter", "get", t.LiteralExpr(body))
URIGetter(@scheme) -> t.NounExpr(scheme + "__uriGetter")
MapExpr(@assocs) -> mcall("__makeMap", "fromPairs", mcall("__makeList", "run", *[mcall("__makeList", "run", *a) for a in assocs]))
MapExprAssoc(@key @value) -> [key, value]
MapExprExport(nameAndString:pair) -> [t.LiteralExpr(pair[1]), pair[0]]

ListExpr(@items) -> mcall("__makeList", "run", *items)

QuasiExpr(null [qexpr:qs]) -> t.MethodCallExpr(mcall("simple__quasiParser", "valueMaker", qs[0]), "substitute", [mcall("__makeList", "run", *qs[1])])
QuasiExpr(@name [qexpr:qs]) -> t.MethodCallExpr(mcall(name + "__quasiParser", "valueMaker", qs[0]), "substitute", [mcall("__makeList", "run", *qs[1])])

qexpr = (qtext | qehole)*:pairs -> buildQuasi(pairs)
qpatt = (qtext | qehole | qphole)*:pairs -> buildQuasi(pairs)
qtext = QuasiText(:text) -> (text, None, None)
qehole = QuasiExprHole(@expr) -> (None, expr, None)
qphole = QuasiPatternHole(@patt) -> (None, None, patt)

SeqExpr([]) -> None
SeqExpr(@exprs) -> t.SeqExpr(flattenSeqs(exprs))

VerbCurryExpr(@receiver :verb) -> mcall("__makeVerbFacet", "curryCall", receiver, t.LiteralExpr(verb))
GetExpr(@receiver @index) -> t.MethodCallExpr(receiver, "get", index)
FunctionCallExpr(@receiver @args) -> t.MethodCallExpr(receiver, "run", args)
FunctionSendExpr(@receiver @args) -> mcall("M", "send", receiver, t.LiteralExpr("run"), args)
MethodSendExpr(@receiver :verb @args) -> mcall("M", "send", receiver, t.LiteralExpr(verb), mcall("__makeList", "run", *args))
SendCurryExpr(@receiver :verb) -> mcall("__makeVerbFacet", "currySend", receiver, t.LiteralExpr(verb))

Minus(@receiver) -> t.MethodCallExpr(receiver, "negate", [])
LogicalNot(@receiver) -> t.MethodCallExpr(receiver, "not", [])
BinaryNot(@receiver) -> t.MethodCallExpr(receiver, "complement", [])

Pow(@left @right) -> binop("pow", left, right)
Multiply(@left @right) -> binop("multiply", left, right)
Divide(@left @right) -> binop("approxDivide", left, right)
FloorDivide(@left @right) -> binop("floorDivide", left, right)
Remainder(@left @right) -> binop("remainder", left, right)
Mod(Pow(@x @y) @z) -> t.MethodCallExpr(x, "modPow", [y, z])
Mod(MethodCallExpr(@x "pow" [@y]) @z) -> t.MethodCallExpr(x, "modPow", [y, z])
Mod(@left @right) -> binop("mod", left, right)
Add(@left @right) -> binop("add", left, right)
Subtract(@left @right) -> binop("subtract", left, right)
ShiftRight(@left @right) -> binop("shiftRight", left, right)
ShiftLeft(@left @right) -> binop("shiftLeft", left, right)
Till(@left @right) -> mcall("__makeOrderedSpace", "op__till", left, right)
Thru(@left @right) -> mcall("__makeOrderedSpace", "op__thru", left, right)
GreaterThan(@left @right) -> mcall("__comparer", "greaterThan", left, right)
GreaterThanEqual(@left @right) -> mcall("__comparer", "geq", left, right)
AsBigAs(@left @right) -> mcall("__comparer", "asBigAs", left, right)
LessThanEqual(@left @right) -> mcall("__comparer", "leq", left, right)
LessThan(@left @right) -> mcall("__comparer", "lessThan", left, right)
Coerce(@spec @guard) -> t.MethodCallExpr(mcall("ValueGuard", "coerce", guard, t.NounExpr("throw")), "coerce", [spec, t.NounExpr("throw")])

MatchBind(@spec @patt) -> expandMatchBind(self, spec, patt)
Mismatch(@spec @patt) -> t.MethodCallExpr(expandMatchBind(self, spec, patt), "not", [])

Same(@left @right) -> mcall("__equalizer", "sameEver", left, right)
NotSame(@left @right) -> t.MethodCallExpr(mcall("__equalizer", "sameEver", left, right), "not", [])

ButNot(@left @right) -> binop("butNot", left, right)
BinaryOr(@left @right) -> binop("or", left, right)
BinaryAnd(@left @right) -> binop("and", left, right)
BinaryXor(@left @right) -> binop("xor", left, right)

LogicalAnd(@left @right) -> expandLogical(self, left, right, expandAnd)
LogicalOr(@left @right) -> expandLogical(self, left, right, expandOr)

Def(@pattern @exit @expr) -> expandDef(self, pattern, exit, expr, self.nouns)

Forward(@name) !(t.NounExpr(name.args[0].data + "__Resolver")):rname -> t.SeqExpr([
                                            t.Def(t.ListPattern([
                                                      t.FinalPattern(name, None),
                                                      t.FinalPattern(rname, None)],
                                                  None),
                                            None,
                                          mcall("Ref", "promise")),
                                        rname])

Assign(@left @right) = ass(left right)
ass NounExpr(:name) :right -> t.Assign(t.NounExpr(name), right)
ass MethodCallExpr(@receiver @verb @args):left :right !(self.mktemp("ares")):ares -> t.SeqExpr([t.MethodCallExpr(receiver, putVerb(verb), args + [t.Def(t.FinalPattern(ares, None), None, right)]), ares])
ass :left :right -> err("Assignment can only be done to nouns and collection elements", self)


VerbAssign(:verb @target @args) = vass(verb target args)

vass :verb NounExpr(@name) :args -> t.Assign(t.NounExpr(name), mcall(name, verb, *args))
vass :verb MethodCallExpr(@receiver :methVerb :methArgs) :args -> expandCallVerbAssign(self, verb, args, receiver, methVerb, methArgs)
vass :verb :badTarget :args -> verbAssignError(self, badTarget)

AugAssign(@op @left @right) = vass(binops[op] left [right])

Break(null) -> mcall("__break", "run")
Break(@expr) -> mcall("__break", "run", expr)

Continue(null) -> mcall("__continue", "run")
Continue(@expr) -> mcall("__continue", "run", expr)

Return(null) -> mcall("__return", "run")
Return(@expr) -> mcall("__return", "run", expr)

Guard(@expr @subscripts) -> reduce(lambda e, s: t.MethodCallExpr(e, "get", s), subscripts, expr)

#IgnorePattern(@guard) -> t.IgnorePattern(guard)

SamePattern(@value) -> t.ViaPattern(mcall("__matchSame", "run", value), t.IgnorePattern(None))
NotSamePattern(@value) -> t.ViaPattern(mcall("__matchSame", "different", value), t.IgnorePattern(None))

VarPattern(@name @guard) = -> t.VarPattern(name, guard)

BindPattern(@name @guard) -> t.ViaPattern(mcall("__bind", "run", t.NounExpr(name.args[0].data + "__Resolver"), guard), t.IgnorePattern(None))

#FinalPattern(@name @guard) -> t.FinalPattern(name, guard)
SlotExpr(@name) -> slot(name)
SlotPattern(@name null) -> t.ViaPattern(t.NounExpr("__slotToBinding"), t.BindingPattern(name))
SlotPattern(@name @guard) -> t.ViaPattern(t.MethodCallExpr(t.NounExpr("__slotToBinding"), "run", [guard]), t.BindingPattern(name))

MapPattern(@assocs @tail) -> foldr(lambda more, (l, r): t.ViaPattern(l, t.ListPattern([r, more], None)),
                                   tail or t.IgnorePattern(t.NounExpr("__mapEmpty")),
                                   reversed(assocs))

MapPatternAssoc(@key @value) -> [key, value]
MapPatternImport(nameAndString:nameAnd) -> [t.LiteralExpr(nameAnd[1]), nameAnd[0]]
MapPatternOptional(@assoc @default) -> [mcall("__mapExtract", "depr", assoc[0], default), assoc[1]]
MapPatternRequired(@assoc) -> (mcall("__mapExtract", "run", assoc[0]), assoc[1])
ListPattern(@patterns null) -> t.ListPattern(patterns, None)
ListPattern(@patterns @tail) -> t.ViaPattern(mcall("__splitList", "run", t.LiteralExpr(len(patterns))), t.ListPattern(patterns + [tail], None))

SuchThatPattern(@pattern @expr) -> t.ViaPattern(t.NounExpr("__suchThat"),
                                      t.ListPattern([pattern, t.ViaPattern(mcall("__suchThat", "run", expr), t.IgnorePattern(None))], None))

QuasiPattern(null [qpatt:qs]) -> t.ViaPattern(mcall("__quasiMatcher", "run", mcall("simple__quasiParser", "matchMaker", qs[0]), mcall("__makeList", "run", *qs[1])), t.ListPattern(qs[2], None))
QuasiPattern(@name [qpatt:qs]) -> t.ViaPattern(mcall("__quasiMatcher", "run", mcall(name + "__quasiParser", "matchMaker", qs[0]), mcall("__makeList", "run", *qs[1])), t.ListPattern(qs[2], None))

Interface(@doco nameAndString:nameAnd @guard @extends @implements
          InterfaceFunction(:params :resultGuard))
     -> expandInterface(doco, nameAnd[0], nameAnd[1], guard, extends,
                        implements,
                        [self.transform(t.MessageDesc("", "to", "run",
                                                      params, resultGuard))])
Interface(@doco nameAndString:nameAnd @guard @extends @implements @script)
     -> expandInterface(doco, nameAnd[0], nameAnd[1], guard, extends,
                        implements, script)

MessageDesc(@doco @type @verb @paramDescs @guard)
     -> t.HideExpr(mcall("__makeMessageDesc", "run",
                         doco and t.LiteralExpr(doco), t.LiteralExpr(verb),
                         mcall("__makeList", "run", *paramDescs),
                         guard or t.NounExpr("void")))

ParamDesc(name:name @guard) -> mcall("__makeParamDesc", "run", t.LiteralExpr(name), guard or t.NounExpr("any"))


Lambda(@doco @patterns @block) -> t.Object(doco, t.IgnorePattern(None), [None],
                                      t.Script(None,
                                               [t.Method(None, "run", patterns,
                                                         None, block)],
                                               []))

Object(:doco BindPattern(:name :guard):bp :auditors :script):o transform(bp):exName
     transform(t.Object(doco, t.FinalPattern(t.NounExpr(name), None), auditors, script)):exObj
 -> t.Def(exName, None, t.HideExpr(exObj))

Object(@doco @name @auditors Function(@params @guard @block))
    -> t.Object(doco, name, auditors, t.Script(None,
                                  [t.Method(doco, "run", params, guard,
                                       t.Escape(t.FinalPattern(t.NounExpr("__return"), None),
                                           t.SeqExpr([block, t.NounExpr("null")]), None))],
                                  []))

Object(@doco @name @auditors Script(null @methods @matchers)) -> t.Object(doco, name, auditors, t.Script(None, methods, matchers))

Object(@doco VarPattern(@name @guard):vp @auditors Script(@extends @methods @matchers)) transform(vp):exVP
    objectSuper(doco exVP auditors extends methods matchers [slot(t.NounExpr(name))]):o
    -> t.SeqExpr([t.Def(slotpatt(name), None, o), name])

Object(@doco @name @auditors Script(@extends @methods @matchers)) =
    objectSuper(doco name auditors extends methods matchers []):o -> t.Def(name, None, o)

objectSuper :doco :name :auditors :extends :methods :matchers :maybeSlot !(self.mktemp("pair")):p -> t.HideExpr(t.SeqExpr([
       t.Def(t.FinalPattern(t.NounExpr("super"), None),
           None, extends),
       t.Object(doco, name, auditors,
           t.Script(None, methods,
               matchers + [t.Matcher(t.FinalPattern(p, None),
                           mcall("M", "callWithPair", t.NounExpr("super"), p))]))
       ] + maybeSlot))
To(:doco @verb @params @guard @block) -> t.Method(doco, verb, params, guard, t.Escape(t.FinalPattern(t.NounExpr("__return"), None),
                                                  t.SeqExpr([block, t.NounExpr("null")]), None))

For(:key :value @coll @block @catcher)
    -> expandFor(self, key, value, coll, block, catcher)
ListComp(@key @value @iterable @filter @exp) -> expandComprehension(self, key,
                                                    value, iterable, filter, exp,
                                                "__accumulateList")

MapComp(@key @value @iterable @filter @kexp @vexp) -> expandComprehension(self, key,
                                                        value, iterable, filter,
                                                        mcall("__makeList", "run", kexp, vexp),
                                                        "__accumulateMap")

Switch(@expr @matchers) -> expandSwitch(self, expr, matchers)

Try(@tryblock [] null) -> t.HideExpr(tryblock)
Try(@tryblock [(Catch(@p @b) -> (p, b))*:cs]  @finallyblock) kerneltry(expandTryCatch(tryblock, cs) finallyblock)

kerneltry :tryexpr null -> tryexpr
kerneltry :tryexpr :finallyexpr -> t.Finally(tryexpr, finallyexpr)

While(@test @block @catcher) = expandWhile(test block catcher)

expandWhile :test :block :catcher -> t.Escape(t.FinalPattern(t.NounExpr("__break"), None), mcall("__loop", "run", mcall("__iterWhile", "run", t.Object(None, t.IgnorePattern(None), [None], t.Script(None, [t.Method(None, "run", [], None, test)], []))), t.Object("While loop body", t.IgnorePattern(None), [None], t.Script(None, [t.Method(None, "run", [t.IgnorePattern(None), t.IgnorePattern(None)], t.NounExpr("boolean"),  t.SeqExpr([t.Escape(t.FinalPattern(t.NounExpr("__continue"), None), block, None), t.NounExpr("true")]))], []))), catcher)

When([@arg] @block :catchers @finallyblock) expandWhen(arg block catchers finallyblock)
When(@args @block :catchers :finallyblock) expandWhen(mcall("promiseAllFulfilled", "run", t.MethodCallExpr(t.NounExpr("__makeList"), "run", args)) block catchers finallyblock)

expandWhen :arg :block [(Catch(@p @b) -> (p, b))*:catchers] :finallyblock !(self.mktemp("resolution")):resolution kerneltry(expandTryCatch(t.If(mcall("Ref", "isBroken", resolution), mcall("Ref", "broken", mcall("Ref", "optProblem", resolution)), block), catchers) finallyblock):body -> t.HideExpr(mcall("Ref", "whenResolved", arg, t.Object("when-catch 'done' function", t.IgnorePattern(None), [None], t.Script(None, [t.Method(None, "run", [t.FinalPattern(resolution, None)], None, body)], []))))

"""

def flattenSeqs(xs):
    items = []
    for x in xs:
        if x.tag.name == 'SeqExpr' and x.args:
            items.extend(x.args[0].args)
        else:
            items.append(x)
    return items

def expandSwitch(self, expr, matchers):
    sp = self.mktemp("specimen")
    failures = [self.mktemp("failure") for _ in matchers]
    return t.HideExpr(t.SeqExpr([
        t.Def(t.FinalPattern(sp, None),
              None, expr),
        matchExpr(self, matchers, sp, failures)]))

def matchExpr(self, matchers, sp, failures):
    ejs = [self.mktemp("ej") for _ in matchers]
    block = mcall("__switchFailed", "run", sp, *failures)
    for m, fail, ej in reversed(zip(matchers, failures, ejs)):
        block = t.Escape(
            t.FinalPattern(ej, None),
            t.SeqExpr([
                t.Def(m.args[0], ej, sp),
                m.args[1]]),
            t.Catch(t.FinalPattern(fail, None),
                    block))
    return block

def expandTryCatch(tryblock, catchers):
    block = tryblock
    for (patt, catchblock) in catchers:
        block = t.KernelTry(block, patt, catchblock)
    return block

def binop(name, left, right):
    return t.MethodCallExpr(left, name, [right])

def expandLogical(self, left, right, fn):
    leftmap = scope(left).outNames()
    rightmap = scope(right).outNames()
    both = [t.NounExpr(n) for n in leftmap | rightmap]
    result = self.mktemp("ok")
    success = t.MethodCallExpr(t.NounExpr("__makeList"), "run",
                               [TRUE] + [t.BindingExpr(n) for n in both])
    failure = t.MethodCallExpr(t.NounExpr("__booleanFlow"), "failureList", [t.LiteralExpr(len(both))])

    return t.SeqExpr([
        t.Def(t.ListPattern([t.FinalPattern(result, None)] +
                            [t.BindingPattern(n) for n in both], None),
              None,
              fn(left, right, success, failure, leftmap, rightmap)),
        result])

def expandAnd(left, right, success, failure, leftmap, rightmap):
    return t.If(left, t.If(right, success, failure), failure)

def expandOr(left, right, success, failure, leftmap, rightmap):
    broken = mcall("__booleanFlow", "broken")
    def partialFail(failed):
        return t.SeqExpr([t.Def(t.BindingPattern(n), None, broken) for n in failed] + [success])
    rightOnly = [t.NounExpr(n) for n in rightmap - leftmap]
    leftOnly = [t.NounExpr(n) for n in leftmap - rightmap]
    return t.If(left, partialFail(rightOnly), t.If(right, partialFail(leftOnly), failure))

def expandInterface(doco, name, nameStr, guard, extends, implements, script):
    def makeIFace(verb):
        return t.HideExpr(
            mcall("__makeProtocolDesc", verb, doco and t.LiteralExpr(doco),
                  t.MethodCallExpr(
                      t.MethodCallExpr(t.Meta("Context"), "getFQNPrefix", []),
                      "add", [t.LiteralExpr(nameStr + "__T")]),
                  mcall("__makeList", "run", *extends),
                  mcall("__makeList", "run", *implements),
                  mcall("__makeList", "run", *script)))
    if guard:
        return t.MethodCallExpr(
            t.Def(t.ListPattern([name, guard], None), None, makeIFace("makePair")),
                  "get", [t.LiteralExpr(0)])
    else:
        return t.Def(name, None, makeIFace("run"))


def validateFor(self, left, right):
    if left.outNames() & right.namesUsed():
        err("Use on right isn't really in scope of definition", self)
    if right.outNames() & left.namesUsed():
        err("Use on left would get captured by definition on right", self)

def expandFor(self, key, value, coll, block, catcher):
    if key.tag.name == "null":
        key = t.IgnorePattern(None)
    validateFor(self, scope(key).add(scope(value)), scope(coll))
    fTemp = self.mktemp("validFlag")
    kTemp = self.mktemp("key")
    vTemp = self.mktemp("value")
    obj = t.Object(
        "For-loop body", t.IgnorePattern(None),
        [None],
        t.Script(
            None,
            [t.Method(None, "run",
                      [t.FinalPattern(kTemp, None),
                       t.FinalPattern(vTemp, None)],
                      None,
                      t.SeqExpr([
                          mcall("__validateFor", "run", fTemp),
                          t.Escape(
                              t.FinalPattern(t.NounExpr("__continue"), None),
                              t.SeqExpr([
                                  t.Def(key, None, kTemp),
                                  t.Def(value, None, vTemp),
                                  block,
                                  t.NounExpr("null")]),
                              None)]))],
            []))
    return t.Escape(
        t.FinalPattern(t.NounExpr("__break"), None),
        t.SeqExpr([t.Def(
            t.VarPattern(fTemp, None), None,
            t.NounExpr("true")),
                   t.Finally(
                       t.MethodCallExpr(
                           t.NounExpr("__loop"),
                           "run",
                           [coll, obj]),
                       t.Assign(fTemp, t.NounExpr("false"))),
                   t.NounExpr("null")]),
        catcher)


def expandComprehension(self, key, value, coll, filtr, exp, collector):
    if key is None:
        key = t.IgnorePattern(None)
    validateFor(self, scope(exp), scope(coll))
    validateFor(self, scope(key).add(scope(value)), scope(coll))
    fTemp = self.mktemp("validFlag")
    kTemp = self.mktemp("key")
    vTemp = self.mktemp("value")
    skip = self.mktemp("skip")
    kv = [t.Def(key, None, kTemp), t.Def(value, None, vTemp)]
    if filtr:
        value = t.SeqExpr(kv + [t.If(filtr, exp, t.MethodCallExpr(skip, "run", []))])
    else:
        value = t.SeqExpr(kv + [exp])
    obj = t.Object(
        "For-loop body", t.IgnorePattern(None),
        [None],
        t.Script(
            None,
            [t.Method(None, "run",
                      [t.FinalPattern(kTemp, None),
                       t.FinalPattern(vTemp, None),
                       t.FinalPattern(skip, None)],
                      None,
                      t.SeqExpr([
                          mcall("__validateFor", "run", fTemp),
                          value]))],
            []))
    return t.SeqExpr([
        t.Def(
            t.VarPattern(fTemp, None), None,
            t.NounExpr("true")),
        t.Finally(
            t.MethodCallExpr(
                t.NounExpr(collector),
                "run",
                [coll, obj]),
            t.Assign(fTemp, t.NounExpr("false")))])


def expandMatchBind(self, spec, patt):
    pattScope = scope(patt)
    specScope = scope(spec)
    conflicts = pattScope.outNames() & specScope.namesUsed()
    if conflicts:
        err("Use on left isn't really in scope of matchbind pattern: %s" %
            (', '.join(conflicts)), self)

    sp = self.mktemp("sp")
    ejector = self.mktemp("fail")
    result = self.mktemp("ok")
    problem = self.mktemp("problem")
    broken = self.mktemp("b")

    patternNouns = [t.NounExpr(n) for n in pattScope.outNames()]
    return t.SeqExpr([
        t.Def(t.FinalPattern(sp, None), None, spec),
        t.Def(
            t.ListPattern([t.FinalPattern(result, None)] +
                          [t.BindingPattern(n) for n in patternNouns], None),
            None,
            t.Escape(
                t.FinalPattern(ejector, None),
                t.SeqExpr([
                    t.Def(patt, ejector, sp),
                    mcall("__makeList", "run",
                          TRUE, *[t.BindingExpr(n) for n in patternNouns])]),
                t.Catch(t.FinalPattern(problem, None),
                        t.SeqExpr([
                            t.Def(slotpatt(broken), None,
                                  mcall("Ref", "broken", problem)),
                            mcall("__makeList", "run",
                                  FALSE, *([t.BindingExpr(broken)] * len(patternNouns)))])))),
        result])

def broke(br, ex):
    return t.Def(t.FinalPattern(br, None),
                 mcall("Ref", "broken", mcall("__makeList", "run", ex)))


def slot(n):
    return t.MethodCallExpr(t.BindingExpr(n), 'get', [])

def slotpatt(n):
    return t.ViaPattern(t.NounExpr("__slotToBinding"), t.BindingPattern(n))

binops =  {
    "Add": "add",
    "Subtract": "subtract",
    "Multiply": "multiply",
    "Divide": "approxDivide",
    "Remainder": "remainder",
    "Mod": "mod",
    "Pow": "pow",
    "FloorDivide": "floorDivide",
    "ShiftRight": "shiftRight",
    "ShiftLeft": "shiftLeft",
    "BinaryAnd": "and",
    "BinaryOr": "or",
    "BinaryXor": "xor",
    "ButNot": "butNot"
}

reifier = r"""
TempNounExpr(@basename @o) -> reifyNoun(self, basename, o)
"""
def reifyNoun(self, base, o):
    k = (base, o)
    if k in self.cache:
        return self.cache[k]

    self.id += 1
    noun = "%s__%s" % (base, self.id)
    while noun in self.nouns:
        self.id += 1
        noun = "%s__%s" % (base, self.id)
    self.nouns.add(noun)
    n = t.NounExpr(noun)
    self.cache[k] = n
    return n

cycleRenamer = r"""
NounExpr(@name) (?(name in self.renamings) -> self.renamings[name]
                |                          -> t.NounExpr(name))
"""

def expand(term, scope=None):
    e = Expander([term])
    e.scope = scope
    e.nouns = set()
    e.counter = 0
    expanded = e.apply("transform")[0]
    r = Reifier([expanded])
    r.nouns = set(e.nouns)
    r.cache = {}
    r.id = 0
    reified = r.apply("transform")[0]
    return reified

def mktemp(self, name):
    self.counter += 1
    return t.TempNounExpr(name, self.counter)

StaticScopeTransformer = TreeTransformerGrammar.makeGrammar(computeStaticScopeRules, "StaticScopeTransformer").createParserClass(TreeTransformerBase, globals())


Expander = TreeTransformerGrammar.makeGrammar(expander, name="EExpander").createParserClass(TreeTransformerBase, globals())
Expander.mktemp = mktemp

Reifier = TreeTransformerGrammar.makeGrammar(reifier, name="Reifier").createParserClass(TreeTransformerBase, globals())

CycleRenamer = TreeTransformerGrammar.makeGrammar(cycleRenamer, name="CycleRenamer").createParserClass(TreeTransformerBase, globals())
