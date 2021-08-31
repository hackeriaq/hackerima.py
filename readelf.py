#!/usr/bin/python
# program to gather link dependencies of ELF files for compliance analysis
# Stew Benedict <stewb@linuxfoundation.org>
# Jeff Licquia <licquia@linuxfoundation.org>
# copyright 2010 Linux Foundation

import sys
import os
import re
import string
import sqlite3
import optparse
version = '0.0.8'

# Get Django loaded.  This has to be done outside a function so
# the setup is only done once and the modules are globally available.

os.environ["DJANGO_SETTINGS_MODULE"] = "compliance.settings"

django_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(django_path)

from compliance.linkage.models import StaticSymbol
from compliance.linkage.views import create_test_record, delete_test_record, \
                                     process_results, mark_test_done

# Custom exceptions.

class NotELFError(StandardError):
    pass

class StaticSymbolError(StandardError):
    pass

class NoDebugInfoError(StaticSymbolError):
    pass

class NoStaticDataError(StaticSymbolError):
    pass

# Globals.

usage_line = "usage: %prog [options] <file/dir tree to examine> [recursion depth]"

command_line_options = [
    optparse.make_option("-c", action="store_true", dest="do_csv", 
                         default=False, help="output in csv format"),
    optparse.make_option("-d", action="store_true", dest="do_db", 
                         default=False, help="write the output into the results database"),
    optparse.make_option("-s", action="store", type="string", dest="target",
                         metavar="DIR", help="directory tree to search"),
    optparse.make_option("--comments", action="store", type="string", dest="comments",
                         default = '', help="test comments (when writing to database)"),
    optparse.make_option("--project", action="store", type="string", dest="project",
                         default = '', help="project name (when writing to database)"),
    optparse.make_option("--no-static", action="store_false", dest="do_static",
                         default=True, help="don't look for static dependencies")
]

depth = 1
do_csv = False
do_static = True
do_search = False
do_db = False
project = ''
comments = ''
testid = None
lastfile = ''
parentid = 0
parentlibid = 0

def show_result(result):
    sys.stdout.write(result + "\n")
    sys.stdout.flush()
    if do_db:
        # we need all these for the recursive case, now that we write a line at a time to the db
        global lastfile, parentid, parentlibid
        errmsg = ''

        errmsg, lastfile, parentid, parentlibid = process_results(result, testid, lastfile, parentid, parentlibid)
        
        if errmsg:
            show_error(errmsg)
  
def show_error(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()
    if do_db:
        delete_test_record(testid)
    sys.exit(1)

def bad_depth():
    show_error("Recursion depth must be a positive number")

def dep_path(target, dep):
    # readelf gives us the lib, but not the path to check during recursion
    ldcall = "ldd " + target

    for lddata in os.popen(ldcall).readlines():
        if re.search("statically linked", lddata):
            return "NONE"

        elif re.search(dep, lddata):
            ldlist = string.split(lddata)
            if len(ldlist) > 2:
                # path is the 3rd field, usually...
                dpath = ldlist[2]
            else:
                dpath = ldlist[0]
            # but this may be a symlink
            dpath = os.path.realpath(dpath)
            break

    return dpath        

def find_static_library(func):
    "Given a symbol, return the most likely static library it's from."

    found_libs = StaticSymbol.objects.filter(symbol=func)
    return [x.libraryname for x in found_libs]

def static_deps_check(target):
    "Look for statically linked dependencies."

    # State enumeration for debug parser.
    FIND_NEXT = 1
    FIND_NAME = 2

    # The algorithm here is pretty basic.  We grab a complete symbol list
    # and debug information.  Any symbols that aren't covered by debug
    # information are considered to be source from static libraries.

    # Check that we have static symbol data.
    static_data_count = StaticSymbol.objects.all().count()
    if static_data_count < 1:
        raise NoStaticDataError, "no static symbol data"

    # Read the functions from the symbol list.
    symlist = [ x.split() for x in os.popen("readelf -s " + target) ]
    symlist = [ x for x in symlist if len(x) == 8 ]
    sym_funcs = set([ x[7] for x in symlist if x[3] == "FUNC" ])

    # Read the functions from the debug information.
    found_debug_info = False
    debuginfo = os.popen("readelf -wi " + target)
    debug_funcs = set()
    debugstate = FIND_NEXT
    for line in debuginfo:
        if len(line) < 2:
            continue

        found_debug_info = True

        if debugstate == FIND_NAME:
            if line[1] == "<":
                debugstate = FIND_NEXT
            else:
                match = re.match(r'\s+<.+>\s+(.+?)\s+:\s+\(.+\):\s+(.+)$', line)
                if match:
                    (field, value) = match.group(1, 2)
                    if field == "DW_AT_name":
                        debug_funcs.add(value.strip())
                        debugstate = FIND_NEXT

        if debugstate == FIND_NEXT and line[1] == "<":
            match = re.search(r'\((.+)\)$', line)
            if match and match.group(1) == "DW_TAG_subprogram":
                found_name = None
                debugstate = FIND_NAME

    # If no debug information was reported, report the error.
    if not found_debug_info:
        raise NoDebugInfoError, "no debugging information was found"

    # Get the functions in the symbol list that have no debug info.
    staticsym_funcs = sym_funcs - debug_funcs

    # For each function, figure out where it came from.
    staticlib_list = []
    staticlib_multiples = {}
    for func in staticsym_funcs:
        libs = find_static_library(func)
        if len(libs) == 1:
            if libs[0] not in staticlib_list:
                staticlib_list.append(libs[0])
        elif len(libs) > 1:
            staticlib_multiples[func] = libs

    # Symbols found in multiple libraries should be handled last.
    # We pick the first library only if none of the libs has
    # been picked yet.
    for func in staticlib_multiples:
        found = False
        for lib in staticlib_multiples[func]:
            if lib in staticlib_list:
                found = True
                break
        if not found:
            staticlib_list.append(staticlib_multiples[func][0])

    # Format and return the list.
    staticlib_list.sort()
    staticlib_results = [ x + " (static)" for x in staticlib_list ]
    return staticlib_results

def deps_check(target):
    deps = []
    # run the "file" command and see if it's ELF
    filetype = os.popen("file " + target).read()
    
    if re.search("ELF", filetype):
        if not re.search("statically linked", filetype):
            elfcall = "readelf -d " + target
            for elfdata in os.popen(elfcall).readlines():
                # lines we want all have "NEEDED"
                if re.search("NEEDED", elfdata):
                    # library is the 5th field
                    dep = string.split(elfdata)[4]
                    dep = dep.strip("[]")
                    deps.append(dep)

        if do_static:
            try:
                deps.extend(static_deps_check(target))
            except StaticSymbolError:
                deps.append("WARNING: Could not check for static dependencies")

    else:
        raise NotELFError, "not an ELF file"

    return deps

# non-recursive case
def print_deps(target, deps):
    global rbuff
    csvstring = ''
    spacer = ''

    if len(deps) < 1:
        return

    if do_csv:
        csvstring += str(1) + "," + target
        for dep in deps:
            csvstring += "," + dep
        show_result(csvstring)

    else:
        show_result(spacer + "[" + str(1) + "]" + target + ":")
        spacer += "  "
        for dep in deps:
            show_result(spacer + dep)

def print_dep(dep, indent):
    spacer = 2 * indent * " "
    if not do_csv:
        show_result(spacer + dep)

def print_path_dep(parent, soname, dep, indent):
    csvstring = ''
    spacer = (indent - 1) * "  "
    token = "[" + str(indent) + "]"
    if not do_csv:
        show_result(spacer + token + parent + ":")
    else:
        csvstring += str(indent) + "," + parent + ","
        # indent = level, treat level 1 slightly differently
        if indent != 1 and soname:
            csvstring += soname + ","
        csvstring += dep
        show_result(csvstring)

def dep_loop(parent, soname, dep, level):
    if level > depth:
        return

    if level == 1:
        print_path_dep(parent, soname, dep, level)
        print_dep(dep, level)
    else:
        print_path_dep(parent, soname, dep, level)
        print_dep(dep, level)

    if not re.search('(static)', dep):
        target = dep_path(parent, dep)
        childdeps = deps_check(target)
    else:
        childdeps = []

    if len(childdeps) > 0:
        for childdep in childdeps:
            dep_loop(target, dep, childdep, level + 1)

def db_test_record(target, target_dir):
    testid = 0
    user = os.environ["USER"]
    testid = create_test_record(do_search, not(do_static), depth, target, target_dir, user, project, comments)
    return testid

def main():
    opt_parser = optparse.OptionParser(usage=usage_line, 
                                       version="%prog version " + version,
                                       option_list=command_line_options)
    (options, args) = opt_parser.parse_args()

    if len(args) == 0 or len(args) > 2:
        opt_parser.error("improper number of non-option arguments")

    # prog_ndx_start is the offset in argv for the file/dir and recursion
    prog_ndx_start = 1
    found = 0
    parent = ''
    target_dir = ''
    global do_csv, depth, do_static, do_search, do_db, project, comments, testid

    do_static = options.do_static
    do_csv = options.do_csv
    do_db = options.do_db
    # force csv in this case
    if do_db:
        do_csv = True
    project = options.project
    comments = options.comments

    if options.target:
        do_search = True
        target = options.target
        target_file = args[0]
        target_dir = target
        if not os.path.isdir(target):
            show_error(target + " does not appear to be a directory...")
    else:
        target = args[0]
        if not(os.path.isdir(target) or os.path.isfile(target)):
            show_error(target + " does not appear to be a directory or file...")

    # sanity check on recursion level
    if len(args) == 1:
        depth = 1
        
    else:
        try:
            recursion_arg = args[1]
            depth = int(recursion_arg)
        except:
            bad_depth()

        if depth < 1:
            bad_depth()

    if do_db:
        testid = db_test_record(target, target_dir)

    if os.path.isdir(target):
        # walk the directory tree and find ELF files to process
        for path, dirs, files in os.walk(target):
            for filename in files:
                if (do_search and (filename == target_file)) or not(do_search):
                    candidate = os.path.join(path, filename)
                    if os.path.isfile(candidate):
                        try:
                            deps = deps_check(candidate)
                        except NotELFError:
                            deps = []    
                        
                        if len(deps) > 0:
                            if depth == 1:
                                print_deps(candidate, deps)
                            # do recursion if called for
                            else:
                                for dep in deps:
                                    dep_loop(candidate, None, dep, 1)
                    if do_search and (filename == target_file):
                        found = 1                   
                        break

        if do_search and not found:
            show_error(target_file + " was not found in " + target + " ...")

    else:
	    # single file, just check it and exit
        # top level deps
        parent = target
        target_file = target
        target_dir = ''

        try:
            deps = deps_check(target)
        except NotELFError:
            show_error("not an ELF file...")

        if depth == 1:
            print_deps(target, deps)

        # do recursion if called for       
        else:
            for dep in deps:
                dep_loop(parent, None, dep, 1)

    # close the db test record
    if do_db:
        mark_test_done(testid)

    sys.exit(0)

if __name__=='__main__':
    main()

