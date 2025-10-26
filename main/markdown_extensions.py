"""
Markdown extension for converting LaTeX to MathML.

Vendored from https://gitlab.com/parcifal/l2m4m/-/blob/develop/l2m4m.py
"""

import re

from latex2mathml import converter
from markdown import Extension
from markdown.blockprocessors import BlockProcessor
from markdown.inlinepatterns import Pattern


class LaTeX2MathMLExtension(Extension):
    _RE_LATEX = r"\$([^$]+)\$"

    def extendMarkdown(self, md):
        md.inlinePatterns.register(LatexPattern(self._RE_LATEX), "latex-inline", 170)
        md.parser.blockprocessors.register(
            LatexBlockProcessor(md.parser), "latex-block", 170
        )


class LatexPattern(Pattern):
    def handleMatch(self, m):
        return converter.convert_to_element(m.group(2))


class LatexBlockProcessor(BlockProcessor):
    _RE_LATEX_START = [r"^\s*\${2}", r"^\s*\\\["]

    _RE_LATEX_END = [r"\${2}\s*$", r"\\\]\s*$"]

    def __init__(self, parser):
        super().__init__(parser)

        self._mode = 0

    def test(self, _, block):
        """
        Indicated whether the specified block starts a block of LaTeX.
        """
        for i, start in enumerate(self._RE_LATEX_START):
            if not re.search(start, block):
                continue

            self._mode = i
            return True

        return False

    def run(self, parent, blocks):
        """
        Convert all subsequent blocks of LaTeX to MathML. Cancel conversion
        in case no ending block is found.
        """
        start = self._RE_LATEX_START[self._mode]
        end = self._RE_LATEX_END[self._mode]

        for i, block in enumerate(blocks):
            if not re.search(start, block):
                continue

            text = "\n".join([blocks.pop(j) for j in range(0, i + 1)])
            text = re.sub(start, "", text)
            text = re.sub(end, "", text)

            converter.convert_to_element(text, display="block", parent=parent)

            return True

        return False
