"""cmdparser - A simple command parsing library."""


import cmd
import shlex


class ParseError(Exception):
    """Error parsing command specification."""
    pass



class MatchError(Exception):
    """Raised internally if a command fails to match the specification."""
    pass



class CallTracer(object):

    def __init__(self, trace, name):
        self.trace = trace
        self.name = name
        if trace is not None:
            trace.append(">>> " + name)


    def __del__(self):
        if self.trace is not None:
            self.trace.append("<<< " + self.name)



class ParseItem(object):
    """Base class for all items in a command specification."""

    def finalise(self):
        """Called when an object is final.

        The default does nothing, derived classes can raise ParseError if
        the object isn't valid as it stands for any reason.
        """
        pass


    def add(self, child):
        """Called when a child item is added.

        The default is to disallow children, derived classes can override.
        """
        raise ParseError("children not allowed")


    def add_alternate(self):
        """Called to add a new alternate option.

        The default is to disallow alternates, derived classes can override.
        """
        raise ParseError("alternates not allowed")


    def match(self, compare_items, fields=None, completions=None, trace=None):
        """Called during the match process.

        Should attempt to match item's specification against command-line
        supplied in compare_items and either return compare_items with
        consumed items removed, or raise MatchError if the command-line
        doesn't match.

        If the item has consumed a command-line argument, it should store
        it against the item's name in the fields dict if that parameter is
        not None. If the completions field is not None and compare_items
        is empty (i.e. just after the matched string) then the item should
        store a list of valid tokens in the completions set just prior
        to raising MatchError - this only applies to items which accept
        a list of valid values, items which match any string should leave
        the set alone (it's used for tab-completion).

        The trace argument, if supplied, should be a list. As each class's
        match() function is entered or left, a string representing it
        is appended to the list.

        The default is to raise a MatchError, derived classes should override.
        """
        raise MatchError(compare_items)


    def check_match(self, items, fields=None, trace=None):
        """Return None if the specified command-line is valid and complete.

        If the command-line doesn't match, the first non-matching item is
        returned, or the empty string if the command was incomplete.

        Calling code should typically use this instead of calling match()
        directly. Derived classes shouldn't typically override this method.
        """
        try:
            unparsed = self.match(items, fields=fields, trace=trace)
            if not unparsed:
                return None
        except MatchError, e:
            unparsed = e.args[0]
        if unparsed:
            return unparsed[0]
        else:
            return ""


    def get_completions(self, items):
        """Return set of valid tokens to follow partial command-line in items.

        Calling code should typically use this instead of calling match()
        directly. Derived classes shouldn't typically override this method.
        """
        try:
            completions = set()
            self.match(items, completions=completions)
        except MatchError:
            pass
        return completions



class Sequence(ParseItem):
    """Matches a sequential series of items, each of which must match."""

    def __init__(self):
        self.items = []


    def finalise(self):
        """See ParseItem.finalise()."""

        if not self.items:
            raise ParseError("empty sequence")
        for item in self.items:
            item.finalise()


    def add(self, child):
        """See ParseItem.add()."""

        assert isinstance(child, ParseItem)
        self.items.append(child)


    def match(self, compare_items, fields=None, completions=None, trace=None):
        """See ParseItem.match()."""

        tracer = CallTracer(trace, "Sequence")
        for item in self.items:
            compare_items = item.match(compare_items, fields=fields,
                                       completions=completions)
        return compare_items



class Alternation(ParseItem):
    """Matches any of a list of alternative Sequence items.

    Alternation instances can also be marked optional by setting the "optional"
    parameter to True in the constructor - this menas that if none of the
    options match, they'll return success without consuming any items instead of
    raising MatchError.

    Note that matching is greedy with no back-tracking, so if an optional item
    matches the command line argument(s) will always be consumed even if this
    leads to a MatchError later in the string which wouldn't have occurred had
    the optional item chosen to match nothing instead.
    """

    def __init__(self, optional=False):
        self.optional = optional
        self.options = []
        self.add_alternate()


    def finalise(self):
        """See ParseItem.finalise()."""

        if not self.options:
            raise ParseError("empty alternation")
        for option in self.options:
            option.finalise()


    def add(self, child):
        """See ParseItem.add()."""

        assert isinstance(child, ParseItem)
        self.options[-1].add(child)


    def add_alternate(self):
        """See ParseItem.add_alternate()."""

        self.options.append(Sequence())


    def match(self, compare_items, fields=None, completions=None, trace=None):
        """See ParseItem.match()."""

        tracer = CallTracer(trace, "Alternation")
        remaining = compare_items
        for option in self.options:
            try:
                return option.match(compare_items, fields=fields,
                                    completions=completions)
            except MatchError, e:
                if len(e.args[0]) < len(remaining):
                    remaining = e.args[0]
        if self.optional:
            return compare_items
        else:
            raise MatchError(remaining)



class Token(ParseItem):
    """Matches a single, fixed item.

    This class also doubles as the base class for any application-specific items
    which should match one or more fixed strings (the list can change over time,
    but at any point in time there's a deterministic list of valid options).
    Such derived classes should simply override get_values().
    """

    def __init__(self, name, token=None):
        self.name = name
        self.token = name if token is None else token


    def get_values(self):
        """Return the current list of valid tokens.

        Derived classes should override this method to return the full list of
        every valid token. This method is invoked on demand with no caching
        (though there is nothing to stop derived instances doing their own
        caching should it be required).
        """
        return [self.token]


    def match(self, compare_items, fields=None, completions=None, trace=None):
        """See ParseItem.match()."""

        tracer = CallTracer(trace, "Token(%s)" % (self.name,))
        if not compare_items:
            if completions is not None:
                completions.update(self.get_values())
            raise MatchError([])
        for value in self.get_values():
            if compare_items and compare_items[0] == value:
                if fields is not None:
                    fields[self.name] = value
                return compare_items[1:]
        raise MatchError(compare_items)



class AnyToken(ParseItem):
    """Matches any single item."""

    def __init__(self, name):
        self.name = name


    def match(self, compare_items, fields=None, completions=None, trace=None):
        tracer = CallTracer(trace, "AnyToken(%s)" % (self.name,))
        if not compare_items:
            raise MatchError([])
        if fields is not None:
            fields[self.name] = compare_items[0]
        return compare_items[1:]



class AnyTokenString(ParseItem):
    """Matches the remainder of the command string."""

    def __init__(self, name):
        self.name = name


    def match(self, compare_items, fields=None, completions=None, trace=None):
        tracer = CallTracer(trace, "AnyTokenString(%s)" % (self.name,))
        if not compare_items:
            raise MatchError([])
        if fields is not None:
            fields[self.name] = " ".join(compare_items)
        return []



def parse_spec(spec, ident_factory=None):

    stack = [Sequence()]
    token = ""
    name = None
    ident = False

    for num, char in enumerate(spec, 1):

        # Perform correctness checks.
        if ident and (char in ":()[]|<" or char.isspace()):
            raise ParseError("invalid in identifier at char %d" % (num,))
        if char == ">" and not (ident and token):
            raise ParseError("only valid after identifier at char %d" % (num,))
        if char in "|])" and not (stack and isinstance(stack[-1], Alternation)):
            raise ParseError("invalid outside alternation at char %d" % (num,))
        if char in ")]" and char != ")]"[stack[-1].optional]:
            raise ParseError("mismatched brackets at char %d" % (num,))
        if char == ":" and not token:
            raise ParseError("empty token name at char %d" % (num,))

        # Save out any current token.
        if (char in "()[]<>|" or char.isspace()) and token and not ident:
            stack[-1].add(Token(token, name))
            token = ""
            name = None

        # Process character.
        if char == "(":
            stack.append(Alternation())
        elif char == "[":
            stack.append(Alternation(optional=True))
        elif char == "<":
            ident = True
        elif char == "|":
            stack[-1].add_alternate()
        elif char in ")]":
            alt = stack.pop()
            alt.finalise()
            stack[-1].add(alt)
        elif char == ">":
            item = None
            if token.endswith("..."):
                item = AnyTokenString(token[:-3])
            elif ident_factory is not None:
                item = ident_factory(token)
            if item is None:
                item = AnyToken(token)
            stack[-1].add(item)
            ident = False
            token = ""
        elif char == ":":
            name = token
            token = ""
        elif not char.isspace():
            token += char

    if len(stack) != 1 or ident:
        raise ParseError("incomplete specification")
    if token:
        stack[-1].add(Token(token, name))
    stack[-1].finalise()
    return stack.pop()



def cmd_class_decorator(cls):
    """Decorates a cmd.Cmd class and adds completion methods.

    Any method which has been decorated with cmd_do_method_decorator() will
    have a tag added which is detected by this class decorator, and the
    appropriate completion methods added.
    """

    for method in dir(cls):
        data_attr = getattr(method, "_cmdparser_data", None)
        if data_attr is not None:
            command_string, tree = data_attr
            completer_method_name = "complete_" + command_string

            def completer_method(self, text, line, begidx, endidx):
                items = shlex.split(line[:begidx])
                return [i for i in tree.get_completions(items)
                        if i.startswith(text)]

            setattr(cls, completer_method_name, completer_method)

    return cls



def cmd_do_method_decorator(method):
    """Decorates a do_XXX method with command parsing code.

    Also marks the method as requiring completion, suitable for the later
    class decorator to insert a completion method.
    """

    # Work out command name.
    if not method.func_name.startswith("do_"):
        raise ParseError("method name %r invalid" % (method.func_name,))
    command_string = method.func_name[3:]

    # Retrieve command specification.
    spec = ""
    for spec_line in method.__doc__.splitlines():
        if not spec_line:
            if spec:
                break
            continue
        spec += "\n" + spec_line.strip()

    # TODO: Could easily re-flow the help text here, for example stripping
    #       off any whitespace prefix beyond the first line.

    # Flag commands with no command spec as an error.
    if not spec:
        raise ParseError("%s: no command spec found" % (method.func_name,))

    # Convert specification into parse tree.
    try:
        tree = parse_spec(spec)
        starts = tree.get_completions([])
        if len(starts) != 1:
            raise ParseError("command spec must have unique initial token")
        token = starts.pop()
        if token != command_string:
            raise ParseError("command spec initial token %r must match command"
                             " from method %r" % (token, command_string))
    except ParseError, e:
        raise ParseError("%s: %s" % (method.func_name, e))

    # Build replacement method.
    def do_wrapper(self, args):

        split_args = [command_string] + shlex.split(args)
        fields = {}
        check = tree.check_match(split_args, fields=fields)
        if check is None:
            return method(self, split_args, fields)
        else:
            if check:
                print "Invalid command (failed at %r)" % (check,)
            else:
                print "Incomplete command"

    do_wrapper.__doc__ = method.__doc__
    do_wrapper._cmdparser_data = (command_string, tree)

    return do_wrapper
