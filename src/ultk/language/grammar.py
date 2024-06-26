import random
import re
from collections import defaultdict
from collections.abc import Sequence
from itertools import product
from typing import Any, Callable, Generator, TypedDict
from functools import cached_property

from dataclasses import dataclass, field
from yaml import load

try:
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader

from ultk.language.language import Expression
from ultk.language.semantics import Meaning, Universe


@dataclass(frozen=True)
class Rule:
    """Basic class for a grammar rule.  Grammar rules in ULTK correspond
    to functions.  One can think of a grammar as generating complex functions from
    more basic ones.

    Attributes:
        lhs: left-hand side of the rule (can be anything)
            conceptually, the output type of a function
        rhs: right-hand side; assumed to be an iterable
            conceptually, a list of types of inputs
        func: a callable, the function to be computed when a node with this rule is executed
        name: name of the function
        weight: a relative weight to assign to this rule
            when added to a grammar, all rules with the same LHS will be weighted together
    """

    name: str
    lhs: Any
    rhs: Sequence | None
    func: Callable = lambda *args: None
    weight: float = 1.0

    def is_terminal(self) -> bool:
        """Whether this is a terminal rule.  In our framework, this means that RHS is empty,
        i.e. there are no arguments to the function.
        """
        return self.rhs is None

    def __str__(self) -> str:
        out_str = f"{str(self.lhs)} -> {self.name}"
        if self.rhs is not None:
            out_str += f"({', '.join(str(typ) for typ in self.rhs)})"
        return out_str


# We need to use unsafe hash here, because the class needs to be both mutable and hashable (e.g., see https://github.com/CLMBRs/ultk/blob/main/src/ultk/effcomm/agent.py#L30).
@dataclass(eq=True, kw_only=True, unsafe_hash=True)
class GrammaticalExpression(Expression):
    """A GrammaticalExpression has been built up from a Grammar by applying a sequence of Rules.
    Crucially, it is _callable_, using the functions corresponding to each rule.

    A GrammaticalExpression, when called, takes in a Referent.  Because of this, a Meaning can
    be generated by specifying a Universe (which contains Referents).

    Attributes:
        rule_name: name of the top-most function
        func: the function
        children: child expressions (possibly empty)
    """

    rule_name: str
    func: Callable
    children: tuple | None

    def yield_string(self) -> str:
        """Get the 'yield' string of this term, i.e. the concatenation
        of the leaf nodes.

        This is useful for thinking of a `Grammar` as generating derivation trees for
        an underlying CFG.  This method will then generate the strings generated by
        the corresponding CFG.
        """
        if self.children is None:
            return str(self)
        return "".join(child.yield_string() for child in self.children)

    def evaluate(self, universe: Universe) -> Meaning:
        # TODO: this presupposes that the expression has type Referent -> bool.  Should we generalize?
        # and that leaf nodes will take Referents as input...
        # NB: important to use `not self.meaning` and not `self.meaning is None` because of how
        # Expression.__init__ initializes an "empty" meaning if `None` is passed
        if not self.meaning:
            self.meaning = Meaning(
                tuple(referent for referent in universe.referents if self(referent)),
                universe,
            )
        return self.meaning

    def call_on_universe(self, universe: Universe) -> tuple[Any, ...]:
        """Call this expression on all elements of a universe, and return a tuple of the results."""
        return tuple(self(referent) for referent in universe.referents)

    def add_child(self, child) -> None:
        if self.children is None:
            self.children = tuple([child])
        else:
            self.children = self.children + (child,)

    @cached_property
    def antimeaning(self) -> Meaning:
        """Get the antimeaning of this expression, i.e. the set of all referents for which
        the expression evaluates to False."""

        return Meaning(set(self.meaning.universe.referents) - set(self.meaning.referents),
                       self.meaning.universe)
    
    def draw_referent(self, antimeaning=False):
        """Get a random referent from the meaning's referents."""
        if antimeaning:
            return random.choice(list(self.antimeaning.referents))
        return random.choice(list(self.meaning.referents)) 

    def to_dict(self) -> dict:
        the_dict = super().to_dict()
        the_dict["grammatical_expression"] = str(self)
        the_dict["length"] = len(self)
        return the_dict

    def __call__(self, *args):
        if self.children is None:
            return self.func(*args)
        return self.func(*(child(*args) for child in self.children))

    def __len__(self):
        length = 1
        if self.children is not None:
            length += sum(len(child) for child in self.children)
        return length

    def __lt__(self, other) -> bool:
        children = self.children or tuple([])
        other_children = other.children or tuple([])
        return (self.rule_name, self.form, self.func, children) < (
            other.rule_name,
            other.form,
            other.func,
            other_children,
        )

    def __str__(self):
        out_str = self.rule_name
        if self.children is not None:
            out_str += f"({', '.join(str(child) for child in self.children)})"
        return out_str


class UniquenessArgs(TypedDict):
    """Arguments for specifying uniqueness of GrammaticalExpressions in a Grammar.

    Attributes:
        unique_expressions: a dictionary in which to store unique Expressions
        key: a function used to evaluate uniqueness
        compare_func: a comparison function, used to decide which Expression to add to the dict
            new Expressions will be added as values to `unique_dict` only if they are minimal
            among those sharing the same key (by `unique_key`) according to this func
    """

    unique_expressions: dict[Any, dict[Any, GrammaticalExpression]]
    key: Callable[[GrammaticalExpression], Any]
    compare_func: Callable[[GrammaticalExpression, GrammaticalExpression], bool]


class Grammar:
    """At its core, a Grammar is a set of Rules with methods for generating GrammaticalExpressions."""

    def __init__(self, start: Any):
        # _rules: nonterminals -> list of rules
        self._rules: dict[Any, list[Rule]] = defaultdict(list)
        # name -> rule, for fast lookup in parsing
        self._rules_by_name: dict[str, Rule] = {}
        self._start = start

    def __iter__(self):
        # Return an iterator object
        return iter(self._rules_by_name.items())

    def __next__(self):
        # Return the next rule in the iterator
        # Raise StopIteration if there are no more rules
        raise StopIteration

    def add_rule(self, rule: Rule):
        self._rules[rule.lhs].append(rule)
        if rule.name in self._rules_by_name:
            raise ValueError(
                f"Rules of a grammar must have unique names. This grammar already has a rule named {rule.name}."
            )
        self._rules_by_name[rule.name] = rule

    def parse(
        self,
        expression: str,
        opener: str = "(",
        closer: str = ")",
        delimiter: str = ",",
    ) -> GrammaticalExpression:
        """Parse a string representation of an expression of a grammar.
        Note that this is not a general-purpose parsing algorithm.  We assume that the strings are of the form
            parent_name(child1_name, ..., childn_name)
        where parent_name is the name of a rule of this grammar that has a length-n RHS, and that
        childi_name is the name of a rule for each child i.

        Args:
            expression: string in the above format

        Returns:
            the corresponding GrammaticalExpression
        """
        # see nltk.tree.Tree.fromstring for inspiration
        # tokenize string roughly by splitting at open brackets, close brackets, and delimiters
        open_re, close_re, delimit_re = (
            re.escape(opener),
            re.escape(closer),
            re.escape(delimiter),
        )
        name_pattern = f"[^{open_re}{close_re}{delimit_re}]+"
        token_regex = re.compile(
            rf"{name_pattern}{open_re}|{name_pattern}|{delimit_re}\s*|{close_re}"
        )

        # stack to store the tree being built
        stack = []

        for match in token_regex.finditer(expression):
            # strip trailing whitespace if needed
            token = match.group().strip()
            # start a new expression
            if token[-1] == opener:
                name = token[:-1]
                stack.append(
                    GrammaticalExpression(
                        rule_name=name,
                        func=self._rules_by_name[name].func,
                        children=tuple(),
                    )
                )
            # finish an expression
            elif token == delimiter or token == closer:
                # finished a child expression
                # TODO: are there edge cases that distinguish delimiter from closer?
                child = stack.pop()
                stack[-1].add_child(child)
            else:
                # primitive, no children, just look up
                stack.append(
                    GrammaticalExpression(
                        rule_name=token,
                        func=self._rules_by_name[token].func,
                        children=None,
                    )
                )
        if len(stack) != 1:
            raise ValueError("Could not parse string {expression}")
        return stack[0]

    def generate(self, lhs: Any = None) -> GrammaticalExpression:
        """Generate an expression from a given lhs."""
        if lhs is None:
            lhs = self._start
        rules = self._rules[lhs]
        the_rule = random.choices(rules, weights=[rule.weight for rule in rules], k=1)[
            0
        ]
        children = (
            None
            if the_rule.rhs is None
            else tuple([self.generate(child_lhs) for child_lhs in the_rule.rhs])
        )
        # if the rule is terminal, rhs will be empty, so no recursive calls to generate will be made in this comprehension
        return GrammaticalExpression(
            rule_name=the_rule.name, func=the_rule.func, children=children
        )

    def enumerate(
        self,
        depth: int = 8,
        lhs: Any = None,
        uniqueness_args: UniquenessArgs | None = None,
    ) -> Generator[GrammaticalExpression, None, None]:
        """Enumerate all expressions from the grammar up to a given depth from a given LHS.
        This method also can update a specified dictionary to store only _unique_ expressions, with
        a user-specified criterion of uniqueness.

        Args:
            depth: how deep the trees should be
            lhs: left hand side to start from; defaults to the grammar's start symbol
            uniqueness_args: a dictionary specifying the parameters for uniqueness:
                unique_dict: a dictionary in which to store unique Expressions
                key: a function used to evaluate uniqueness
                compare_func: a comparison function, used to decide which Expression to add to the dict
                    new Expressions will be added as values to `unique_dict` only if they are _minimal_
                    among those sharing the same key (by `unique_key`) according to this func

        Yields:
            all GrammaticalExpressions up to depth
        """
        if lhs is None:
            lhs = self._start
        cache: defaultdict = defaultdict(list)
        for num in range(depth):
            yield from self.enumerate_at_depth(num, lhs, uniqueness_args, cache)

    def enumerate_at_depth(
        self,
        depth: int,
        lhs: Any,
        uniqueness_args: UniquenessArgs | None = None,
        cache: dict | None = None,
    ) -> Generator[GrammaticalExpression, None, None]:
        """Enumerate GrammaticalExpressions for this Grammar _at_ a fixed depth."""

        if cache is None:
            cache = defaultdict(list)

        # enumerate from cache if we've seen these args before
        args_tuple = (depth, lhs)
        if args_tuple in cache:
            yield from cache[args_tuple]
        else:
            do_unique = uniqueness_args is not None
            if uniqueness_args is not None:
                unique_dict = uniqueness_args["unique_expressions"]
                key = uniqueness_args["key"]

                def add_unique(expression: GrammaticalExpression) -> bool:
                    """Add an expression to the unique_dict, if it is unique and shortest by the compare_func.
                    Return the outcome boolean."""
                    expr_key = key(expression)
                    # if the current expression has not been generated yet
                    # OR it is "less than" the current entry, add this one
                    if expr_key not in unique_dict[lhs] or uniqueness_args[
                        "compare_func"
                    ](expression, unique_dict[lhs][expr_key]):
                        unique_dict[lhs][expr_key] = expression
                        return True
                    return False

            # keep a meaning -> expr dict but also depth -> expr dict
            if depth == 0:
                for rule in self._rules[lhs]:
                    if rule.is_terminal():
                        cur_expr = GrammaticalExpression(
                            rule_name=rule.name, func=rule.func, children=None
                        )
                        if not do_unique or add_unique(cur_expr):
                            cache[args_tuple].append(cur_expr)
                            yield cur_expr
            else:
                for rule in self._rules[lhs]:
                    # can't use terminal rules when depth > 0
                    if rule.rhs is None:
                        continue

                    # get lists of possible depths for each child
                    for child_depths in product(range(depth), repeat=len(rule.rhs)):
                        if max(child_depths) < depth - 1:
                            continue
                        # get all possible children of the relevant depths
                        # unique by depth?!?!
                        children_iter = product(
                            *[
                                self.enumerate_at_depth(
                                    child_depth, child_lhs, uniqueness_args, cache
                                )
                                for child_depth, child_lhs in zip(
                                    child_depths, rule.rhs
                                )
                            ]
                        )
                        for children in children_iter:
                            cur_expr = GrammaticalExpression(
                                rule_name=rule.name, func=rule.func, children=children
                            )
                            if not do_unique or add_unique(cur_expr):
                                cache[args_tuple].append(cur_expr)
                                yield cur_expr

    def get_unique_expressions(
        self,
        depth: int,
        unique_key: Callable[[GrammaticalExpression], Any],
        compare_func: Callable[[GrammaticalExpression, GrammaticalExpression], bool],
        lhs: Any = None,
        max_size: float = float("inf"),
    ) -> dict[GrammaticalExpression, Any]:
        """Get all unique GrammaticalExpressions, up to a certain depth, with a user-specified criterion
        of uniqueness, and a specified comparison function for determining which Expression to save when there's a clash.
        This can be used, for instance, to measure the minimum description length of some
        Meanings, by using expression.evaluate(), which produces a Meaning for an Expression, as the
        key for determining uniqueness, and length of the expression as comparison.

        This is a wrapper around `enumerate`, but which produces the dictionary of key->Expression entries
        and returns it.  (`enumerate` is a generator with side effects).

        For Args, see the docstring for `enumerate`.

        Note: if you additionally want to store _all_ expressions, and not just the unique ones, you should
        directly use `enumerate`.

        Returns:
            dictionary of {key: GrammaticalExpression}, where the keys are generated by `unique_key`
            The GrammticalExpression which is the value will be the one that is minimum among
            `compare_func` amongst all Expressions up to `depth` which share the same key
        """
        unique_dict: dict[Any, dict[Any, GrammaticalExpression]] = defaultdict(dict)
        uniqueness_args: UniquenessArgs = {
            "unique_expressions": unique_dict,
            "key": unique_key,
            "compare_func": compare_func,
        }
        if lhs is None:
            lhs = self._start
        # run through generator, each iteration will update unique_dict
        for _ in self.enumerate(
            depth,
            lhs=lhs,
            uniqueness_args=uniqueness_args,
        ):
            if len(unique_dict) == max_size:
                break
            pass
        return unique_dict[lhs]

    def get_all_rules(self) -> list[Rule]:
        """Get all rules as a list."""
        rules = []
        for lhs in self._rules:
            rules.extend(self._rules[lhs])
        return rules

    def __str__(self):
        return "Rules:\n" + "\n".join(f"\t{rule}" for rule in self.get_all_rules())

    @classmethod
    def from_yaml(cls, filename: str):
        """Read a grammar specified in a simple YAML format.

        Expected format:

        ```
        start: bool
        rules:
        - lhs: bool
          rhs:
          - bool
          - bool
          name: "and"
          func: "lambda p1, p2 : p1 and p2"
        - lhs: bool
          rhs:
          - bool
          - bool
          name: "or"
          func: "lambda p1, p2 : p1 or p2"
        ```

        Note that for each fule, the value for `func` will be passed to
        `eval`, so be careful!

        Arguments:
            filename: file containing a grammar in the above format
        """
        with open(filename, "r") as f:
            grammar_dict = load(f, Loader=Loader)
        grammar = cls(grammar_dict["start"])
        for rule_dict in grammar_dict["rules"]:
            if "func" in rule_dict:
                # TODO: look-up functions from a registry as well?
                rule_dict["func"] = eval(rule_dict["func"])
            if "weight" in rule_dict:
                rule_dict["weight"] = float(rule_dict["weight"])
            grammar.add_rule(Rule(**rule_dict))
        return grammar
