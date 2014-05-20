import os
import sys
from contextlib import contextmanager

import clang.cindex
from clang.cindex import CursorKind

import vim

class Traverser(object):
    def __init__(self):
        self._output = None 
    
    def traverse(self, cursor):
        self._start_traversal(cursor)
        output = self._output
        self._output = None
        return output 

    def _traversal_fn(self, cursor, parent):
        raise NotImplementedError

    def _start_traversal(self, cursor):
        iterate_cursor(cursor, self._traversal_fn)

class NamespaceTraverser(Traverser):
    def __init__(self, source_file):
        self._source_file = source_file
        self._output = []

    def _traversal_fn(self, cursor, parent):
        if cursor.location is not None and cursor.location.file is not None:
            if cursor.location.file.name == self._source_file:
                if cursor.kind == CursorKind.NAMESPACE:
                    self._output.append(cursor)
                    return True
            else:
                return False
        return True

class PrecedingFunctionTraverser(Traverser):
    def __init__(self, source_file, find_fn):
        self._source_file = source_file
        self._find_fn = find_fn
        self._found_fn = False
        self._output = None

    def _traversal_fn(self, cursor, parent):
        if self._output is not None:
            return False
        if cursor.location is not None and cursor.location.file is not None:
            if cursor.location.file.name == self._source_file:
                if is_cursor_function(cursor):
                    if cursor.canonical == self._find_fn.canonical:
                        self._found_fn = True
                    elif self._found_fn:
                        self._output = cursor
                        return False
            else:
                return False

        return True

class DefinitionTraverser(Traverser):
    def __init__(self, source_file, find_fn):
        self._source_file = source_file
        self._find_fn = find_fn
        self._output = None

    def _traversal_fn(self, cursor, parent):
        if self._output is not None:
            return False
        if cursor.location is not None and cursor.location.file is not None:
            if cursor.location.file.name == self._source_file:
                if is_cursor_function(cursor):
                    if cursor.canonical == self._find_fn.canonical:
                        self._output = cursor
                        return False
            else:
                return False
        return True

def create_translation_unit(index, source, unsaved_data=[]):
    return index.parse(None, [source] + ['-xc++', '-std=c++11', '-I/usr/lib/clang/3.4/include'], \
            unsaved_data, \
            clang.cindex.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES)


def get_cursor_from_location(tu, location):
    cursor = clang.cindex.Cursor.from_location(tu, location)
    return cursor

def get_corresponding_file(file_name, header=True):
    file = file_name.split('.')
    ext = file[len(file)-1]
    file = file[:len(file)-1]

    header_ext = ['.hpp', '.hxx', '.h']
    source_ext = ['.cpp', '.cxx', '.c']
    
    if header:
        if ext in header_ext:
            return file_name
        else:
            file.append('h')
            return '.'.join(file)

    else:
        if ext in source_ext:
            return file_name
        else:
            file.append('cpp')
            return '.'.join(file)

def get_header_file(file_name):
    return get_corresponding_file(file_name, True)
def get_source_file(file_name):
    return get_corresponding_file(file_name, False)

def get_buffer_with_name(name):
    for buf in vim.buffers:
        if buf.name == name:
            return buf

def is_cursor_function(cursor):
    if cursor.kind == CursorKind.FUNCTION_DECL or \
            cursor.kind == CursorKind.FUNCTION_TEMPLATE or \
            cursor.kind == CursorKind.CXX_METHOD or \
            cursor.kind == CursorKind.DESTRUCTOR or \
            cursor.kind == CursorKind.CONSTRUCTOR:
        return True
    return False

def is_scope_block(cursor):
    return cursor.kind in [
        clang.cindex.CursorKind.NAMESPACE,
        clang.cindex.CursorKind.UNION_DECL,
        clang.cindex.CursorKind.STRUCT_DECL,
        clang.cindex.CursorKind.ENUM_DECL,
        clang.cindex.CursorKind.CLASS_DECL,
        clang.cindex.CursorKind.UNEXPOSED_DECL,
        clang.cindex.CursorKind.CLASS_TEMPLATE,
        clang.cindex.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION
    ]

def get_function_cursor_from_location(tu, location):
    cursor = get_cursor_from_location(tu, location)

    while cursor is not None:
        if is_cursor_function(cursor):
            break
        else:
            cursor = cursor.lexical_parent
    return cursor

def error(str):
    sys.stderr.write(str)

def iterate_cursor(cursor, fn, parent=None):
    ret = fn(cursor, parent)
    if ret is True:
        for child in cursor.get_children():
            iterate_cursor(child, fn, cursor)

def format_type_name(old_name):
    new_name = old_name
    for i in range(len(old_name)):
        ch = old_name[i]
        if ch == '*' or ch == '&':
            if i > 0 and old_name[i-1] == ' ':
                new_name = new_name[:i-1] + new_name[i:]
                break
    return new_name

def get_args_list(fn_cursor):
    arg_string = []
    args = []

    for i, arg in enumerate(fn_cursor.get_arguments()):
        arg_type = format_type_name(arg.type.spelling)
        arg_name = arg.spelling
        arg_string.append('{0} {1}'.format(arg_type, arg_name))
        if arg_name == '':
            arg_string[i] = arg_string[i][:len(arg_string[i])-1]

    return ', '.join(arg_string)

def make_function_header(fn_cursor):
    args_list = get_args_list(fn_cursor)
    name = fn_cursor.spelling
    return_type = fn_cursor.result_type.spelling

    fn_header = []

    if fn_cursor.kind != CursorKind.CONSTRUCTOR and \
            fn_cursor.kind != CursorKind.DESTRUCTOR:
        fn_header.extend([format_type_name(return_type),  ' '])

    class_name = get_member_class_name(fn_cursor)
    if class_name is not None and class_name != '':
        fn_header.extend([class_name,  "::"])

    fn_header.append('{0}({1})'.format(name, args_list))

    #clang.cindex doesn't seem to expose many specifiers for functions,
    #so try to find them in the token stream.
    depth = 0
    for t in fn_cursor.get_tokens():
        if t.spelling == '{':
            break
        elif t.spelling == '(':
            depth += 1
        elif t.spelling == ')':
            depth -= 1
        elif t.spelling == 'const' and depth == 0:
            fn_header.append(' const')
        elif t.spelling == 'noexcept' and depth == 0:
            fn_header.append(' noexcept')

    return ''.join(fn_header)

def get_member_class_name(cursor):
    cur = cursor.semantic_parent
    name = []
    while cur is not None:
        if cur.kind == CursorKind.CLASS_DECL or \
            cur.kind == CursorKind.CLASS_TEMPLATE:
            name.append(cur.spelling)
        cur = cur.semantic_parent

    if len(name) > 0:
        return '::'.join(name)
    return None

def get_output_location(tu, fn_cursor, out_file, header_file):
    parent = fn_cursor.semantic_parent
    inner_namespace = None
    while parent is not None:
        if parent.kind == CursorKind.NAMESPACE:
            inner_namespace = parent
            break
        parent = parent.semantic_parent
    
    traverser = PrecedingFunctionTraverser(header_file, fn_cursor)
    prev_fn = traverser.traverse(fn_cursor.semantic_parent)

    line = 0

    #Try to put the new function above the function below it in the header
    if prev_fn:
        traverser = DefinitionTraverser(out_file, prev_fn)
        fn_def = traverser.traverse(tu.cursor)
        if fn_def is not None:
            line = fn_def.extent.start.line

    #Otherwise, put it at the bottom of the innermost namespace
    if line == 0:
        traverser = NamespaceTraverser(out_file)
        namespace_list = traverser.traverse(tu.cursor)
        
        inner_namespace = None
        if len(namespace_list) > 0:
            inner_namespace = namespace_list[len(namespace_list) -1]

        line = 0
        if inner_namespace:
            last_line = inner_namespace.extent.end.line

    #If neither works, just put it at the end, which -1 happens to do

    return (inner_namespace, line - 1)

def generate_method_stub(tu, cursor, out_file, header_file, buffer):
    header_string = make_function_header(cursor)

    namespace, line = get_output_location(tu, cursor, out_file, header_file)
    if line < 0:
        line = len(buffer)
    
    fn_string = '\n'.join([header_string, '{', ' ', '}', ' '])

    write_method(fn_string, buffer, line)
    return True

def write_method(fn_string, buffer, line):
    buffer[line:line] = fn_string.split('\n')
    command = 'normal! {0}G'.format(line + 3)
    vim.command(command)

def source_location_from_position(tu, file_name, line, col):
    file = clang.cindex.File.from_name(tu, file_name)
    location = clang.cindex.SourceLocation.from_position(tu, file, line, col)
    return location

def get_function_cursor_on_line(tu, location, buffer):
    cursor = get_function_cursor_from_location(tu, location)
    if cursor is None:
        pos = find_fn_name_from_line(buffer[location.line-1])
        if pos:
            location = clang.cindex.SourceLocation.from_position(tu,\
                    location.file, location.line, pos)
            cursor = get_function_cursor_from_location(tu, location)

    return cursor

def build_unsaved_data(files):
    unsaved_data = []

    for file in files:
        if file:
            buf = get_buffer_with_name(file)
            if buf is not None:
                unsaved_data.append((file, '\n'.join(buf)))

    return unsaved_data

def generate_under_cursor():
    file_name = vim.eval("expand('%')")
    _, line, col, _ = vim.eval("getpos('.')")
    line = int(line)
    col = int(col)

    name = os.path.abspath(file_name)

    header_file = get_header_file(name)
    source_file = get_source_file(name)

    unsaved_data = build_unsaved_data([header_file, source_file])

    if source_file:
        parse_file_name = source_file
    else:
        parse_file_name = header_file
            
    index = clang.cindex.Index.create()
    tu = create_translation_unit(index, parse_file_name, unsaved_data)

    location = source_location_from_position(tu, name, line, col)

    cursor = get_function_cursor_on_line(tu, location, vim.current.buffer)

    if cursor is None:
        error('Unable to find a function at the location specified')
        return

    buffer = get_buffer_with_name(source_file)
    if buffer is None:
        vim.command('e {0}'.format(source_file))
        buffer = vim.current.buffer
    else:
        vim.command('b! {0}'.format(source_file))

    generate_method_stub(tu, cursor, parse_file_name, header_file, buffer)

def find_fn_name_from_line(str):
    last_parenthesis = str.rfind(')')
    i = last_parenthesis
    depth = 0
    while i > 0:
        if str[i] == ')':
            depth += 1
        elif str[i] == '(':
            depth -= 1
        if depth == 0:
            return i - 1
        i -= 1
    return None
    