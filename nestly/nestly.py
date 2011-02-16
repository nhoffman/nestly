import collections
import errno
import glob
import json
import re
import os

## internals
def d_of_jsonfile(fname):
    with open(fname, 'r') as ch:
        return json.load(ch)

def d_to_jsonfile(fname, d):
    with open(fname, 'w') as ch:
        ch.write(json.dumps(d, indent=4)+"\n")

def nvd_to_jsonfile(fname, d):
    d_to_jsonfile(fname, dict((k, v.val) for k, v in d.iteritems()))

def create_dir(dirname):
    try:
        os.makedirs(dirname)
    except OSError, e:
        if e.errno != errno.EEXIST:
            raise


## public

NV = collections.namedtuple('NamedValue', 'name val')

## nv makers: these are various ways of making nv's out of path names
def file_nv(path):
    """make an nv which takes its name from the basename of the path with the suffix taken off"""
    base, _ = os.path.splitext(path)
    return NV(name=os.path.basename(base), val=path)

def dir_nv(path):
    """make an nv which takes its name from the basename"""
    return NV(name=os.path.basename(path), val=path)

def none_nv(path):
    """make a noname nv, which then will not make a directory"""
    return NV(name=None, val=path)


## functions

def path_list_of_pathpair(path, filel):
    return (os.path.join(path, x) for x in filel)

def nonempty_glob(g):
    globbed = glob.glob(g)
    if not globbed:
        raise IOError("empty glob: "+g)
    return globbed

def collect_globs(path, globl):
    return [f for g in path_list_of_pathpair(path, globl) for f in nonempty_glob(g)]

def filter_dir(pathl):
    return [path for path in pathl if os.path.isdir(path)]

# the all_* functions make lambdas that don't do anything interesting with their argument
def all_choices(how, path, filel):
    """used when we can list all of the files of interest"""
    return lambda _: map(how, path_list_of_pathpair(path, filel))

def all_globs(how, path, globl):
    """used when we want all of a certain list of globs"""
    return lambda _: map(how, collect_globs(path, globl))

def all_dir_globs(how, path, globl):
    """just take the directories out of the globs"""
    return lambda _: map(how, filter_dir(collect_globs(path, globl)))

def mirror_dir(start_path, start_paraml, control):
    """mirror a directory tree"""
    # recur by taking all globs from previous dir
    def aux(paraml):
        if 1 < len(paraml):
            control[paraml[1]] = (
                lambda c: map(dir_nv, filter_dir(collect_globs(c[paraml[0]].val, "*"))))
            aux(paraml[1:])
    # start recursion by doing all directories
    if start_paraml:
        control[start_paraml[0]] = all_dir_globs(dir_nv, start_path, ["*"])
        aux(start_paraml)

# we choose to strip the extension and then replace all slashes with dashes
def dirname_of_path(path):
    (base,_) = os.path.splitext(path)
    return(re.sub("/","-",base))

# the actual recursion
def _aux_build(control, paraml, wd):
    if not paraml:
        nvd_to_jsonfile(os.path.join(wd, 'control.json'), control)
        return

    cur, rest = paraml[0], paraml[1:]
    for nv in control[cur](control):
        level_control = dict(control, **{cur: nv})
        if nv.name:
            level_wd = os.path.join(wd, nv.name)
            create_dir(level_wd)
        else:
            level_wd = wd
        _aux_build(level_control, rest, level_wd)

def build(complete):
    destdir = complete["destdir"]
    create_dir(destdir)
    control = complete["control"]
    _aux_build(control, control.keys(), destdir)