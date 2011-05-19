import argparse
import json
import logging
import os
import os.path
import shlex
import shutil
import subprocess
import sys


# Constants to be used as defaults.
MAX_PROCS = 2                    # Set the default maximum number of child processes that can be spawned.
DRYRUN = False                   # Run in dryrun mode, default is False.


def _terminate_procs(procs):
    """
    Terminate all processes in the process dictionary
    """
    logging.warn("Stopping all remaining processes")
    for proc, g in procs.values():
        logging.debug("[%s] SIGTERM", proc.pid)
        proc.terminate()
    sys.exit(1)


def invoke(max_procs, data, json_files):
    procs = {}
    files = iter(json_files)
    while True:
        while len(procs) < max_procs:
            try:
                json_file = files.next()
            except StopIteration:
                if not procs:
                    return
                break
            g = worker(data, json_file)
            try:
                proc = g.next()
            except StopIteration:
                continue
            except OSError:
                # OSError thrown when command couldn't be started
                if data['stop_on_error']:
                    _terminate_procs(procs)
                    return
            else:
                procs[proc.pid] = proc, g

        pid, status = os.wait()

        # Pull the actual exit status - high byte of 16-bit number
        exit_status = status >> 8
        proc, g = procs.pop(pid)

        try:
            g.next()
        except StopIteration:
            pass
        else:
            raise ValueError('worker generators should only yield once')

        # Check exit status, cancel jobs if stop_on_error specified and
        # non-zero
        if exit_status:
            logging.warn('[%s] Finished with non-zero exit status %s',
                    pid, exit_status)
            if data['stop_on_error']:
                _terminate_procs(procs)
                break
        else:
            logging.info("[%s] Finished with %s", pid, exit_status)


def template_subs_file(in_file, out_fobj, d):
    """
    Substitute template arguments in in_file from variables in d, write the
    result to out_fobj.
    """
    with open(in_file, 'r') as in_fobj:
        for line in in_fobj:
            out_fobj.write(line.format(**d))


def worker(data, json_file):
    """
    Handle parameter substitution and execute command as child process.
    """
    # PERHAPS TODO: Support either full or relative paths.
    with open(json_file) as fp:
        d = json.load(fp)
    json_directory = os.path.dirname(json_file)
    def p(*parts):
        return os.path.join(json_directory, *parts)

    # STDOUT and STDERR will be writtne to a log file in each job directory.
    log_file = data['log_file']

    # A template file will be written in each job directory, including the
    # substitution that was performed..
    savecmd_file = data['savecmd_file']

    # if a template file is being used, then we write out to it
    template_file = data['template_file']
    if template_file:
        output_template = p(os.path.basename(template_file))
        with open(output_template, 'w') as out_fobj:
            template_subs_file(template_file, out_fobj, d)

        # Copy permissions to destination
        shutil.copymode(template_file, output_template)

    work = data['template'].format(**d)

    if savecmd_file:
        with open(p(savecmd_file), 'w') as command_file:
            command_file.write(work + "\n")

    # View what actions will take place in dryrun mode.
    if data['dryrun']:
        logging.info("%s - Dry run of %s\n", p(), work)
    else:
        try:
            with open(p(log_file), 'w') as log:
                pr = subprocess.Popen(shlex.split(work), stdout=log,
                                      stderr=log, cwd=p())
                logging.info('[%d] Started %s in %s', pr.pid, work, p())
                yield pr
        except Exception, e:
            # Seems useful to print the command that failed to make the traceback
            # more meaningful.
            # Note that error output could get mixed up if two processes encounter errors
            # at the same instant
            logging.error("%s - Error executing %s - %s", p(), work, e)
            raise e

def extant_file(x):
    """
    'Type' for argparse - checks that file exists but does not open.
    """
    if not os.path.exists(x):
        raise argparse.ArgumentError("{0} does not exist".format(x))
    return x


def parse_arguments():
    """
    Grab options and json files.
    """
    max_procs = MAX_PROCS
    dryrun = DRYRUN
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format='%(asctime)s * %(levelname)s * %(message)s')

    parser = argparse.ArgumentParser(description='jsonrun.py - substitute values into a template and run commands.')
    parser.add_argument('--local', dest='local_procs', type=int,
            help='Run a maximum of N processes in parallel locally.',
            required=True)
    parser.add_argument('--template', dest='template', metavar="'template text'",
            help='Command-execution template, e.g. bash {infile}')
    parser.add_argument('--stop-on-error', action='store_true',
            default=False, help="Stop if any process returns non-zero exit "
            "status (default: %(default)s)")
    parser.add_argument('--templatefile', dest='template_file', metavar="FILE",
            help='Command-execution template file path.')
    parser.add_argument('--savecmdfile', dest='savecmd_file',
            help='Name of the file that will contain the command that was executed.')
    parser.add_argument('--logfile', dest='log_file', default='log.txt',
            help='Name of the file that will contain the command that was executed.')
    parser.add_argument('--dryrun', action='store_true', help='Run in dryrun mode, does not execute commands.')
    parser.add_argument('json_files', type=extant_file, nargs='+')
    arguments = parser.parse_args()

    def insufficient_args(complaint):
        parser.print_help()
        parser.exit(1, "\n"+complaint+"\n")

    # Make sure that either a template or a template file was given
    if arguments.template_file:
        # if given a template file, the default is to run the input
        if not arguments.template:
            template = os.path.join('.',
                    os.path.basename(arguments.template_file))
            if not os.access(arguments.template_file, os.X_OK):
                raise SystemExit(
                        "{0} is not executable. Specify a template.".format(
                    arguments.template_file))

    if not (arguments.template or arguments.template_file):
        insufficient_args("Error: Please specify either a template "
                "or a template file")

    logging.info('template: %s', template)

    if arguments.local_procs is not None:
        max_procs = arguments.local_procs

    if arguments.dryrun is not None:
        dryrun = arguments.dryrun

    # Create a dictionary that will be shared amongst all forked processes.
    data = {}
    data['dryrun'] = dryrun
    data['start_directory'] = os.getcwd()
    data['template'] = template
    data['template_file'] = arguments.template_file
    data['savecmd_file'] = arguments.savecmd_file
    data['log_file'] = arguments.log_file
    data['stop_on_error'] = arguments.stop_on_error

    return data, max_procs, arguments.json_files

def main():
    data, max_procs, json_files = parse_arguments()
    invoke(max_procs, data, json_files)
