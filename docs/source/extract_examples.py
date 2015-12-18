'''extract_examples -- extract examples/tests from docs

Usage:

  extract_examples dest.mt section1.rst section2.rst ...

Examples are converted to monte unit tests.
'''
import logging
import doctest

log = logging.getLogger(__name__)


def main(access):
    inputs, write = access()

    p = doctest.DocTestParser()
    for (section, txt) in inputs:
        write(u'\n# {section}\n'.format(section=section))
        caseNames = []
        for (ix, ex) in enumerate(p.get_examples(txt)):
            name = 'test%s_%s' % (section, ix)
            fixup = '.canonical()' if 'm`' in ex.source else ''
            case = caseTemplate.format(name=name,
                                       source=indent(ex.source, levels=3),
                                       fixup=fixup,
                                       want=ex.want.strip())
            caseNames.append(name)
            write(case)

        write(suiteTemplate.format(section=section,
                                   cases=',\n    '.join(caseNames)))

def indent(source, levels):
    lines = source.split('\n')
    indent = ' ' * (levels * 4)
    return '\n'.join(indent + line for line in lines)

caseTemplate = u"""
def {name}(assert):
    object example:
        method test():
            "doc"
{source}

    assert.equal(example.test(){fixup}, {want}{fixup})

"""

suiteTemplate = u"""
unittest([
    {cases}
])

"""


def mkInputs(argv, open, splitext):
    return [(splitext(arg)[0], open(arg).read()) for arg in argv[2:]]


if __name__ == '__main__':
    def _script():
        from io import open
        from sys import argv
        from os.path import splitext

        def access():
            logging.basicConfig(level=logging.INFO)
            dest = argv[1]
            write = open(dest, 'w').write
            return mkInputs(argv, open, splitext), write

        main(access)

    _script()