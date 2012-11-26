#!/usr/bin/env python

import atexit
import cmd
import datetime
import logging
import operator
import optparse
import os
import readline
import shlex
import sys
import textwrap
import time

import cmdparser
import datetimeparse
import tracklib



VERSION = "1.0"
APP_NAME = "TimeTrack"
BANNER = "\n%s %s\n\nType 'help' to list commands.\n" % (APP_NAME, VERSION)
HISTORY_FILE = os.path.expanduser("~/.timetrackhistory")



def get_option_parser():

    usage = "Usage: %prog [options] [<cmd>]"
    parser = optparse.OptionParser(usage=usage,
                                   version="%s %s" % (APP_NAME, VERSION))
    parser.add_option("-d", "--debug", dest="debug", action="store_true",
                      help="enable debug output on stderr")
    parser.add_option("-H", "--skip-history", dest="skip_history",
                      action="store_true",
                      help="don't try to read/write command history")
    parser.add_option("-M", "--mem-db", dest="mem_db", action="store_true",
                      help="use temporary in-memory database (for testing)")
    parser.set_defaults(debug=False, skip_history=False, mem_db=False)
    return parser



class ApplicationError(Exception):
    pass


def multiline_input(prompt):
    """Input multi-line strings without leaving them in history."""

    block = ""
    old_hist_len = readline.get_current_history_length()
    while True:
        line = raw_input(prompt)
        if line.strip() == ".":
            break
        block += line + "\n"
    for i in xrange(readline.get_current_history_length(), old_hist_len, -1):
        readline.remove_history_item(i - 1)
    return block



def format_duration(secs):
    if secs < 60:
        return "%d sec%s" % (secs, "" if secs == 1 else "s")
    elif secs < 3600:
        return "%d min%s" % (secs // 60, "" if (secs//60)==1 else "s")
    else:
        return ("%d hour%s %d min%s" % (secs // 3600,
                                        "" if (secs//3600)==1 else "s",
                                        (secs // 60) % 60,
                                        "" if ((secs//60)%60)==1 else "s"))



def format_duration_since_datetime(dt):
    now = datetime.datetime.now()
    delta = now - dt
    return format_duration(delta.days * 3600 * 24 + delta.seconds)



def format_date(d):
    now = datetime.date.today()
    if now.year == d.year:
        if now.isocalendar()[1] == d.isocalendar()[1]:
            return d.strftime("%a %d")
        else:
            return d.strftime("%a %b %d")
    else:
        return d.strftime("%a %b %d %Y")



def format_datetime(dt):
    now = datetime.datetime.now()
    if now.date() == dt.date():
        return dt.strftime("%H:%M")
    elif now.year == dt.year:
        if now.isocalendar()[1] == dt.isocalendar()[1]:
            return dt.strftime("%a %d %H:%M")
        else:
            return dt.strftime("%a %b %d %H:%M")
    else:
        return dt.strftime("%a %b %d %H:%M %Y")



def display_summary(summary_dict, format_func):
    """Displays a summary object as a list."""

    max_item_len = len("TOTAL")
    total = None
    item_strs = []
    for item, value in sorted(summary_dict.items()):
        max_item_len = max(max_item_len, len(item))
        item_strs.append((item, format_func(value), value))
        if total is None:
            total = value
        else:
            total += value
    if total is None:
        print "No activity to summarise.\n"
        return
    for item, value, raw in sorted(item_strs, key=operator.itemgetter(2),
                                   reverse=True):
        padding = max_item_len - len(item) + 2
        percent = int(round((raw * 100.0) / total, 0)) if total else 0
        print "%s %s [%2d%%] %s" % (item, "." * padding, percent, value)
    padding = max_item_len - len("TOTAL") + 2
    if total is not None:
        print "\nTOTAL %s...... %s\n" % ("." * padding, format_func(total))



def display_diary(diary):
    """Displays list of diary entries by task."""

    print
    if diary.keys() == [None]:
        prefix = ""
    else:
        prefix = "| "
    wrapper = textwrap.TextWrapper(initial_indent=prefix+"| ",
                                   subsequent_indent=prefix+"| ")
    for name, entries in diary.items():
        if not entries:
            continue
        if name is not None:
            print "+----[ %s ]----" % (name,)
            print "|"
        for entry_time, entry_task, entry_desc in entries:
            date_str = format_datetime(entry_time)
            print "%s+---- %s ----[ %s ]----" % (prefix, date_str, entry_task)
            seen_text = False
            para_break = False
            for line in entry_desc.split("\n"):
                if line:
                    if para_break:
                        print prefix + "|"
                        para_break = False
                    print wrapper.fill(line)
                    seen_text = True
                elif seen_text:
                    para_break = True
            print prefix
        print



def display_entries(entries, long_only=False):
    """Displays log entries by task."""

    filter_func = lambda x: True
    if long_only:
        filter_func = lambda x: (i.duration_secs() >= 3600*4)
    rows = [(str(i.entry_id), i.task, format_duration(i.duration_secs()) + " ",
             format_datetime(i.start)) for i in entries if filter_func(i)]
    if not rows:
        print "No entries."
        print
        return
    w = [max(len(row[i]) for row in rows) for i in (0, 1, 2)]
    fmt_str = "[{{0:>{0}}}] {{1:>{1}}} - {{2:.<{2}}}.. {{3}}".format(*w)
    for row in rows:
        print fmt_str.format(*row)
    print



class TaskToken(cmdparser.Token):

    def get_values(self, cmd_instance):
        return set(cmd_instance.db.tasks)


class TagToken(cmdparser.Token):

    def get_values(self, cmd_instance):
        return set(cmd_instance.db.tags)



def cmd_token_factory(token_name):

    if token_name == "task":
        return TaskToken("task")
    elif token_name == "tag":
        return TagToken("tag")
    elif token_name == "period":
        return datetimeparse.PastCalendarPeriodSubtree("period")
    elif token_name == "time":
        return datetimeparse.DateTimeSubtree("time")
    else:
        return None



@cmdparser.CmdClassDecorator()
class CommandHandler(cmd.Cmd):

    def __init__(self, logger, filename=None):
        self.logger = logger
        self.db = tracklib.TimeTrackDB(self.logger, filename=filename)
        readline.set_completer_delims(" \t\n")
        cmd.Cmd.__init__(self)
        self.identchars += "-"
        self.prompt = "ttrack>>> "


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_create(self, args, fields):
        """create ( tag <name> | task <name> [<tag> [...]] )

        Create new tag or task.

        When creating a task, an optional list of one or more tags may be
        specified to apply those tags to the new task without requiring other
        'task' commands.
        """

        try:
            new_name = fields["<name>"][0]
            if args[1] == "task":
                self.db.tasks.add(new_name)
                print "Created task '%s'" % (new_name,)
                for tag in fields.get("<tag>", []):
                    self.db.add_task_tag(new_name, tag)
                    print "Tagged task '%s' with '%s'" % (new_name, tag)
            elif args[1] == "tag":
                self.db.tags.add(new_name)
                print "Created tag '%s'" % (new_name,)
        except tracklib.TimeTrackError, e:
            self.logger.error("create error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_delete(self, args, fields):
        """delete (task <task>|tag <tag>)

        Delete existing tag or task.

        WARNING: Deleting a tag will remove it from all tasks. Deleting a
                 task will also remove associated diary, todo and log entries.
                 DELETION OF ALL ITEMS IS PERMANENT - THERE IS NO UNDO!
        """

        try:
            if args[1] == "task":
                self.db.tasks.discard(fields["<task>"][0])
                print "Deleted task '%s'" % (fields["<task>"][0],)
            elif "tag" in fields:
                self.db.tags.discard(fields["<tag>"][0])
                print "Deleted tag '%s'" % (fields["<tag>"][0],)
        except tracklib.TimeTrackError, e:
            self.logger.error("delete error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_diary(self, args, fields):
        """diary [<entry...>]

        Add a new diary entry to the current task.

        A single-line entry can be specified on the command line, or if the
        entry argument is ommitted then the user is prompted for a potentially
        multi-line diary entry.
        """

        # Check for current task before user enters a possibly long diary
        # entry (there is a race condition here if another process stops
        # the current task before the diary entry is entered, but users
        # aren't expected to have multiple instances open on the same DB
        # and the error should be caught by the underlying layer).
        try:
            task = self.db.get_current_task()
            if task is None:
                self.logger.error("no current task to add diary entry")
                return
        except tracklib.TimeTrackError, e:
            self.logger.error("diary error: %s", e)

        entry = " ".join(i for i in fields.get("<entry...>", []) if i)
        if not entry:
            print "Enter diary entry, finish with a '.' on a line by itself."
            entry = multiline_input("diary> ")
        if not entry:
            self.logger.error("empty entry specified in diary command")
            return

        try:
            self.db.add_diary_entry(entry)
            print "Entry added to task '%s'" % (task,)
        except tracklib.TimeTrackError, e:
            self.logger.error("diary error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_todo(self, args, fields):
        """todo ( done | <task> ) <entry...>

        Add a new todo to the specified task, or mark one on the current task as
        done.
        """

        # Work out task name.
        if args[1] == "done":
            task = None
        elif args[1] == "current":
            task = self.db.get_current_task()
            if task is None:
                self.logger.error("no current task")
                return
        else:
            task = fields["<task>"][0]

        todo_text = " ".join(i for i in fields["<entry...>"] if i)
        if not todo_text:
            self.logger.error("empty entry specified in todo command")
            return

        try:
            if task is None:
                self.db.mark_todo_done(todo_text)
                print "Todo marked as complete: %s" % (todo_text,)
            else:
                self.db.add_task_todo(task, todo_text)
                print "Todo on task '%s': %s" % (task, todo_text)
        except KeyError:
            self.logger.error("no such task: %s", task)
        except tracklib.TimeTrackError, e:
            self.logger.error("diary error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_rename(self, args, fields):
        """rename ( task <task> | tag <tag> ) <new>

        Changes the name of an existing task or tag.
        """

        try:
            new = fields["<new>"][0]
            if args[1] == "tag":
                old = fields["<tag>"][0]
                self.db.tags.rename(old, new)
                print "Tag '%s' renamed to '%s'" % (old, new)
            else:
                old = fields["<task>"][0]
                self.db.tasks.rename(old, new)
                print "Task '%s' renamed to '%s'" % (old, new)
        except KeyError, e:
            self.logger.error("no such tag/task (%s)", e)
        except tracklib.TimeTrackError, e:
            self.logger.error("rename error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_resume(self, args, fields):
        """resume

        Switch to previously-running task.

        If there is no current task, this command switches to the most recently
        active task. If there is a currently active task, this command switches
        to the most recently active task which is different to the current.
        This can be used to start work on a task again after a period of
        inactivity, or to resume work on something after an interruption.
        """

        try:
            prev = self.db.get_previous_task()
            if prev is None:
                self.logger.error("no previous task to resume")
                return
            self.db.start_task(prev)
            print "Resumed task '%s'" % (prev,)

        except tracklib.TimeTrackError, e:
            self.logger.error("status error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_show(self, args, fields):
        """
        show ( [unused] ( tasks [<tag>] | tags )
             | ( todos | diary ) [ task <task> | tag <tag> ] )

        Display available tasks or tags, or show diary entries or todo items.

        This command has two forms, one of which lists tasks or tags, and one
        of which shows todo items or diary entries for either all tasks or
        for a specified task or tag.

        In the first form, the optional 'unused' keyword shows only tasks
        which haven't been active in the past five weeks or tags which have
        no tasks attached to them. When listing tasks, an optional tag can
        be used to filter the list displayed.

        When listing todos, all outstanding todo items are shown, optionally
        filtered by a specified task or tag. When listing diary entries,
        all entries are shown, also optionally filtered by task or tag.
        Note that without filtering all diary entries ever created will be
        listed, which may product significant amounts of output.
        """

        try:
            spec = "Unused" if "unused" in fields else "All"

            # List tasks
            if "tasks" in fields:
                active_tasks = set()
                if "unused" in fields:
                    start = datetime.datetime.now() - datetime.timedelta(35)
                    for entry in self.db.get_task_log_entries(start=start):
                        active_tasks.add(entry.task)
                if "<tag>" in fields:
                    print "%s tasks with tag '%s':" % (spec, fields["<tag>"][0])
                else:
                    print "%s tasks:" % (spec,)
                for task in self.db.tasks:
                    if task in active_tasks:
                        continue
                    tags = self.db.get_task_tags(task)
                    if "<tag>" in fields and fields["<tag>"][0] not in tags:
                        continue
                    if tags:
                        print "  %s (%s)" % (task, ", ".join(tags))
                    else:
                        print "  %s" % (task,)

            # List tags
            elif "tags" in fields:
                print "%s tags:" % (spec,)
                for tag in self.db.tags:
                    tasks = len(self.db.get_tag_tasks(tag))
                    if "unused" in fields and tasks > 0:
                        continue
                    print "  %s (%d task%s)" % (tag, tasks,
                                                "" if tasks==1 else "s")

            # List todos or diary
            elif "todos" in fields:
                task = fields.get("<task>", [None])[0]
                tag = fields.get("<tag>", [None])[0]
                if task is not None:
                    print "Outstanding todos for task " + task
                elif tag is not None:
                    print "Outstanding todos for tag " + tag
                else:
                    print "All outstanding todos"
                entries = self.db.get_pending_todos(tag=tag, task=task)
                display_diary({None: entries})

            else:
                if "<task>" in fields:
                    print "Diary entries for task " + fields["<task>"][0]
                    entry_gen = self.db.get_task_log_entries(
                            tasks=(fields["<task>"][0],))
                elif "<tag>" in fields:
                    print "Diary entries for tag " + fields["<tag>"][0]
                    entry_gen = self.db.get_task_log_entries(
                            tags=(fields["<tag>"][0],))
                else:
                    print "All diary entries"
                    entry_gen = self.db.get_task_log_entries()
                summary_obj = tracklib.TaskSummaryGenerator()
                summary_obj.read_entries(entry_gen, merge_diaries=True)
                display_diary(summary_obj.diary_entries)

        except tracklib.TimeTrackError, e:
            self.logger.error("show error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_start(self, args, fields):
        """start [<task> [<time>]]

        Starts timer on an already defined task.

        If <task> is not specified, the most recently-created task is started.

        If <time> is specified, the task is started as if that were the current
        time. Most sensible time formats should be accepted if the
        "parsedatetime" module is available, otherwise it must be in
        "YYYY-MM-DD hh:mm:ss" format.
        """

        task = fields.get("<task>", [self.db.get_last_created_task()])[0]
        if task is None:
            self.logger.error("no recently-created task to start")
            return

        dt = fields.get("<time>", [None])[0]
        at_str = "" if dt is None else " at %s" % (format_datetime(dt),)
        print "Starting task '%s'%s" % (task, at_str)

        try:
            self.db.start_task(task, at_datetime=dt)
        except KeyError, e:
            self.logger.error("no such task (%s)" % (e,))
        except tracklib.TimeTrackError, e:
            self.logger.error("start error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_status(self, args, fields):
        """status

        Display current task and time spent on it, as well as previous task.
        """

        try:
            prev = self.db.get_previous_task()
            if prev is None:
                print "No previous task."
            else:
                print "Previous task: %s" % (prev,)
            task = self.db.get_current_task()
            if task is None:
                print "No current task."
                return
            start = self.db.get_current_task_start()
            start_str = format_datetime(start)
            dur_str = format_duration_since_datetime(start)
            print "Current task: %s" % (task,)
            print "Started: %s (%s ago)" % (start_str, dur_str)

        except tracklib.TimeTrackError, e:
            self.logger.error("status error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_stop(self, args, fields):
        """stop [<time>]

        Stops timer on current task.

        If <time> is specified, the task is ended as if that were the current
        time. Most sensible time formats should be accepted if the
        "parsedatetime" module is available, otherwise it must be in
        "YYYY-MM-DD hh:mm:ss" format.
        """

        try:
            task = self.db.get_current_task()
            dt = fields.get("<time>", [None])[0]
            at_str = "" if dt is None else " at %s" % (format_datetime(dt),)
            print "Stopping task '%s'%s" % (task, at_str)
            self.db.stop_task(at_datetime=dt)
        except tracklib.TimeTrackError, e:
            self.logger.error("stop error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_summary(self, args, fields):
        """
        summary ( tag (time | switches | diary) [<period>]
                | task [tag <tag>] (time | switches | diary | [long] entries)
                       [<period>] )

        Shows various summary information over a specified period, split by
        either task or tag. In the case of splitting by task, an optional tag
        can be provided which filters the results to tasks with the specified
        tag.

        The information shown for either can be "time" for the total time spent,
        "switches" for the number of context switches (defined as entry into the
        specified tag or task from a different tag or task with less than a
        minute's gap between them) or "diary" to show all diary entries across
        the specified period. When splitting by task only, the "entries" option
        can also be used, which shows raw task log entries. If the optional
        "long" argument is specified, only entries longer than four hours are
        shown - this can be useful for detecting cases where a task should have
        been stopped overnight, for example.

        The <period> specification can only refer to dates (not times) but is
        quite liberal in the specifications it will allow. Note that if the
        period between two dates is specified, the end date is non-inclusive.
        Some examples of phrases which will be accepted: "today", "3 days ago",
        "last month", "December 2010", "week of 25 March 2011" and
        for arbitrary dates "between 15/2/2010 and yesterday".
        """

        if "<period>" in fields:
            start, end = fields["<period>"][0]
        else:
            # Default to the current week.
            start = datetime.date.today()
            start -= datetime.timedelta(start.weekday())
            end = start + datetime.timedelta(7)

        filter_tag = fields.get("<tag>", None)

        try:
            tags_arg = set((filter_tag,)) if filter_tag is not None else None
            inclusive_end = end - datetime.timedelta(1)
            if inclusive_end == start:
                period_name = "on %s" % (format_date(start),)
            else:
                period_name = "between %s and %s" % (format_date(start),
                                                     format_date(inclusive_end))
            if "entries" in fields:
                summary_obj = tracklib.SummaryGenerator()
                entries = self.db.get_task_log_entries(start=start, end=end,
                                                       tags=tags_arg)
                summary_obj.read_entries(entries)
                print "\nLog entries by %s %s:\n" % (args[1], period_name)
                display_entries(summary_obj.entries,
                                long_only=("long" in fields))
            else:
                if args[1] == "tag":
                    summary_obj = tracklib.TagSummaryGenerator()
                else:
                    # We can't supply the tags_arg to get_summary_for_period(),
                    # or the context switches will be wrong in the summary
                    # object (since we'll fail to consider switches from or
                    # to tasks outside our tag filter set).
                    summary_obj = tracklib.TaskSummaryGenerator(tags=tags_arg)
                entries = self.db.get_task_log_entries(start=start, end=end)
                summary_obj.read_entries(entries)
                if "time" in fields:
                    print "\nTime spent per %s %s:\n" % (args[1], period_name)
                    display_summary(summary_obj.total_time, format_duration)
                elif "switches" in fields:
                    print "\nContext switches per %s %s:\n" % (args[1],
                                                               period_name)
                    display_summary(summary_obj.switches, str)
                elif "diary" in fields:
                    print "\nDiary entries by %s %s:\n" % (args[1],
                                                           period_name)
                    display_diary(summary_obj.diary_entries)
                else:
                    assert False, "Invalid summary type: %r" % (args[2],)
        except tracklib.TimeTrackError, e:
            self.logger.error("summary error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_task(self, args, fields):
        """task ( <task> | current ) ( tag | untag ) <tag>

        Adds or removes a tag from the specified task.
        """

        # Work out tag and task names.
        tag = fields["<tag>"][0]
        if "current" in fields:
            task = self.db.get_current_task()
            if task is None:
                self.logger.error("no current task")
                return
        else:
            task = fields["<task>"][0]

        try:
            if "tag" in fields:
                self.db.add_task_tag(task, tag)
                print "Added tag '%s' to '%s'" % (tag, task)
            else:
                self.db.remove_task_tag(task, tag)
                print "Removed tag '%s' from '%s'" % (tag, task)
        except KeyError, e:
            self.logger.error("no such tag/task (%s)", e)
        except tracklib.TimeTrackError, e:
            self.logger.error("tag error: %s", e)


    @cmdparser.CmdMethodDecorator(token_factory=cmd_token_factory)
    def do_entry(self, args, fields):
        """entry <id>  ( start | end ) <time>

        Updates start/end time of specified entry.

        Most sensible formats for <time> should be accepted if the
        "parsedatetime" module is installed, otherwise it must be in
        "YYYY-MM-DD hh:mm:ss" format. To obtain the numeric IDs of log entries
        use "summary task entries [...]".
        """

        try:
            try:
                entry = self.db.get_entry_from_id(int(fields["<id>"][0]))
                dt = fields["<time>"][0]
                print ("setting entry %s for task %s %s time to %s" %
                       (entry.entry_id, entry.task, args[2],
                        dt.strftime("%Y-%m-%d %H:%M:%S")))
                if "start" in fields:
                    entry.start = dt
                else:
                    entry.end = dt
            except ValueError:
                self.logger.error("entry ID must be an integer")
                return
            except KeyError:
                self.logger.error("entry %r doesn't exist" %
                                  (fields["<id>"][0],))
                return
        except tracklib.TimeTrackError, e:
            self.logger.error("entry error: %s", e)


    def do_exit(self, args):
        """
Exit the application.
"""
        return 1


    def do_EOF(self, args):
        """
Exit the application.
"""
        print "exit"
        return self.do_exit(args)


    def emptyline(self):
        """Do nothing on an empty line."""
        return



def get_stderr_logger(app_name, level=logging.WARNING):

    logger = logging.getLogger(app_name)
    stderr_handler = logging.StreamHandler()
    stderr_formatter = logging.Formatter("%(name)s: %(levelname)s -"
                                         " %(message)s")
    stderr_handler.setFormatter(stderr_formatter)
    stderr_handler.setLevel(level)
    logger.addHandler(stderr_handler)

    # Logger accepts all messages, and relies on handlers to set their
    # own thresholds.
    logger.setLevel(1)

    return logger



def main(argv):
    """Main entrypoint."""

    # Parse command-line arguments
    parser = get_option_parser()
    (options, args) = parser.parse_args(argv[1:])

    if options.debug:
        logger = get_stderr_logger("ttrack", logging.DEBUG)
    else:
        logger = get_stderr_logger("ttrack")

    # If not skipping history, read command history file (if any) and
    # register an atexit handler to write it on termination.
    if not options.skip_history:
        atexit.register(readline.write_history_file, HISTORY_FILE)
        try:
            readline.read_history_file(HISTORY_FILE)
        except IOError:
            pass

    try:
        filename = ":memory:" if options.mem_db else None
        interpreter = CommandHandler(logger, filename)
        if args:
            cmdline = []
            for arg in args:
                if " " in arg:
                    cmdline.append('"%s"' % (arg,))
                else:
                    cmdline.append(arg)
            interpreter.onecmd(" ".join(cmdline))
        else:
            first_time = True
            while True:
                try:
                    if first_time:
                        first_time = False
                        interpreter.cmdloop(BANNER)
                    else:
                        interpreter.cmdloop("")
                    break
                except KeyboardInterrupt:
                    print
                    print "(To quit use the 'exit' command or CTRL+D)"

    except ApplicationError, e:
        if logger is not None:
            logger.error(e)
        return 1

    except Exception, e:
        if logger is not None:
            logger.critical("caught exception: %s" % e, exc_info=True)
        return 1

    return 0



if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    finally:
        logging.shutdown()

