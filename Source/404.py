#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
404.
A simple multithreaded dead link crawler.
https://github.com/Beluki/404

ex: python3 404.py http://localhost --threads 2 --internal follow --external ignore --timeout  15 | awk '{ print $2 }' > report.txt
"""


import os
import queue
import sys
import time
import urllib

from contextlib import closing
from queue import Queue
from threading import Thread

from argparse import ArgumentParser, RawDescriptionHelpFormatter


# Information and error messages:

def outln(line):
    """ Write 'line' to stdout, using the platform encoding and newline format. """
    print(line, flush = True)


def errln(line):
    """ Write 'line' to stderr, using the platform encoding and newline format. """
    print('404.py: error:', line, file = sys.stderr, flush = True)


# Non-builtin imports:

try:
    import requests

    from bs4 import BeautifulSoup, SoupStrainer
    from requests import Timeout

except ImportError:
    errln('404 requires the following modules:')
    errln('beautifulsoup4 4.3.2+ - <https://pypi.python.org/pypi/beautifulsoup4>')
    errln('requests 2.7.0+ - <https://pypi.python.org/pypi/requests>')
    sys.exit(1)


# Threads and a thread pool:

class Worker(Thread):
    """
    Thread that pops tasks from a '.todo' Queue, executes them, and puts
    the completed tasks in a '.done' Queue.

    A task is any object that has a run() method.
    Tasks themselves are responsible to hold their own results.
    """

    def __init__(self, todo, done):
        super().__init__()
        self.todo = todo
        self.done = done
        self.daemon = True
        self.start()

    def run(self):
        while True:
            task = self.todo.get()
            task.run()
            self.done.put(task)
            self.todo.task_done()


class ThreadPool(object):
    """
    Mantains a list of 'todo' and 'done' tasks and a number of threads
    consuming the tasks. Child threads are expected to put the tasks
    in the 'done' queue when those are completed.
    """

    def __init__(self, threads):
        self.threads = threads

        self.todo = Queue()
        self.done = Queue()

        self.pending_tasks = 0

    def add_task(self, task):
        """
        Add a new task to complete.
        Can be called after start().
        """
        self.pending_tasks += 1
        self.todo.put(task)

    def start(self):
        """ Start computing tasks. """
        for x in range(self.threads):
            Worker(self.todo, self.done)

    def wait_for_task(self):
        """ Wait for one task to complete. """
        while True:
            try:
                return self.done.get(block = False)

            # give tasks processor time:
            except queue.Empty:
                time.sleep(0.1)

    def poll_completed_tasks(self):
        """ Yield the computed tasks as soon as they are finished. """
        while self.pending_tasks > 0:
            yield self.wait_for_task()
            self.pending_tasks -= 1

        # at this point, all the tasks are completed:
        self.todo.join()


# Tasks:

# A BeautifulSoup strainer that only cares about links/images:
link_strainer = SoupStrainer(lambda name, attrs: name == 'a' or name == 'img')


class LinkTask(object):
    """
    A task that checks one link and optionally parses
    the HTML to get links in the body.
    """
    def __init__(self, link, parse_links, timeout, allow_redirects):
        self.link = link
        self.parse_links = parse_links
        self.timeout = timeout
        self.allow_redirects = allow_redirects

        # will contain the links found in the url body when HTML and parse_links = True:
        self.links = []

        # will hold the status code and the response headers after executing run():
        self.status = None

        # since we run in a thread with its own context
        # exception information is captured here:
        self.exception = None

    def run(self):
        try:
            with closing(requests.get(self.link,
                                      timeout = self.timeout,
                                      allow_redirects = self.allow_redirects,
                                      stream = True)) as response:

                self.status = response.status_code

                # when not looking for links, we have all the information needed:
                if not self.parse_links:
                    return

                # when the status is a client/server error, don't look for links either:
                if 400 <= self.status < 600:
                    return

                # when not html/xml, no links:
                content_type = response.headers.get('content-type', '').strip()
                if not content_type.startswith(('text/html', 'application/xhtml+xml')):
                    return

                # parse:
                soup = BeautifulSoup(response.content, 'html.parser', parse_only = link_strainer, from_encoding = response.encoding)

                # <a href="...">
                for tag in soup.find_all('a', href = True):
                    absolute_link = urllib.parse.urljoin(self.link, tag['href'])
                    self.links.append(absolute_link)

                # <img src="...">
                for tag in soup.find_all('img', src = True):
                    absolute_link = urllib.parse.urljoin(self.link, tag['src'])
                    self.links.append(absolute_link)

        except:
            self.exception = sys.exc_info()


# IO:

# For portability, all output is done in bytes
# to avoid Python default encoding and automatic newline conversion:

def utf8_bytes(string):
    """ Convert 'string' to bytes using UTF-8. """
    return bytes(string, 'UTF-8')


BYTES_NEWLINES = {
    'dos'    : b'\r\n',
    'mac'    : b'\r',
    'unix'   : b'\n',
    'system' : utf8_bytes(os.linesep),
}


def binary_stdout_writeline(line, newline):
    """
    Write 'line' (as bytes) to stdout without buffering
    using the specified 'newline' format (as bytes).
    """
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.write(newline)
    sys.stdout.flush()


# Parser:

def make_parser():
    parser = ArgumentParser(
        description = __doc__,
        formatter_class = RawDescriptionHelpFormatter,
        epilog = 'example: 404.py http://beluki.github.io --internal follow --threads 5',
        usage  = '404.py url [option [options ...]]',
    )

    # positional:
    parser.add_argument('url',
        help = 'url to crawl looking for links')

    # optional:
    parser.add_argument('--external',
        help = 'whether to check, ignore or follow external links (default: check)',
        choices = ['check', 'ignore', 'follow'],
        default = 'check')

    parser.add_argument('--internal',
        help = 'whether to check, ignore or follow internal links (default: check)',
        choices = ['check', 'ignore', 'follow'],
        default = 'check')

    parser.add_argument('--newline',
        help = 'use a specific newline mode (default: system)',
        choices = ['dos', 'mac', 'unix', 'system'],
        default = 'system')

    parser.add_argument('--no-redirects',
        help = 'do not follow redirects, just return the status code',
        action = 'store_true')

    parser.add_argument('--print-all',
        help = 'print all status codes and urls instead of only errors',
        action = 'store_true')

    parser.add_argument('--quiet',
        help = 'do not print statistics to stderr after crawling',
        action = 'store_true')

    parser.add_argument('--threads',
        help = 'number of threads (default: 1)',
        default = 1,
        type = int)

    parser.add_argument('--timeout',
        help = 'seconds to wait for request responses (default: 10)',
        default = 10,
        type = int)

    return parser


# Main program:

def run(url, allow_redirects, internal, external, newline, print_all, quiet, threads, timeout):
    """
    Setup a threadpool and start checking links.
    """
    status = 0

    # create the pool and a task to start at the root:
    pool = ThreadPool(threads)
    pool.add_task(LinkTask(url, True, timeout, allow_redirects))
    pool.start()

    # link cache to avoid following repeating links:
    link_cache = set([url])

    # url domain:
    netloc = urllib.parse.urlparse(url).netloc

    # stats:
    st_total_links = 1
    st_total_internal = 1
    st_total_external = 0
    st_error_task = 0
    st_error_link = 0
    st_start_time = time.process_time()

    # start checking links:
    for task in pool.poll_completed_tasks():

        # error in request:
        if task.exception:
            status = 1
            exc_type, exc_obj, exc_trace = task.exception

            # provide a concise error message for timeouts (common):
            if isinstance(exc_obj, Timeout):
                errln('{} - timeout.'.format(task.link))
            else:
                errln('{} - {}.'.format(task.link, exc_obj))

            st_error_task += 1

        else:
            client_or_server_error = (400 <= task.status < 600)

            if client_or_server_error or print_all:
                output = utf8_bytes('{}: {}'.format(task.status, task.link))
                binary_stdout_writeline(output, newline)

            if client_or_server_error:
                st_error_link += 1

            for link in task.links:

                # ignore client-side fragment:
                link, _ = urllib.parse.urldefrag(link)

                if link not in link_cache:
                    link_cache.add(link)
                    parsed = urllib.parse.urlparse(link)

                    # accept http/s protocols:
                    if not parsed.scheme in ('http', 'https'):
                        continue

                    # internal or external link?
                    if parsed.netloc == netloc:
                        if internal == 'ignore':
                            continue

                        st_total_internal += 1
                        get_links = (internal == 'follow')

                    else:
                        if external == 'ignore':
                            continue

                        st_total_external += 1
                        get_links = (external == 'follow')

                    link_task = LinkTask(link, get_links, timeout, allow_redirects)
                    pool.add_task(link_task)
                    st_total_links += 1

    if not quiet:
        st_end_time = time.process_time() - st_start_time

        print('Checked {} total links in {:.3} seconds.'.format(st_total_links, st_end_time), file = sys.stderr)
        print('{} internal, {} external.'.format(st_total_internal, st_total_external), file = sys.stderr)
        print('{} network/parsing errors, {} link errors.'.format(st_error_task, st_error_link), file = sys.stderr)

    sys.exit(status)


# Entry point:

def main():
    parser = make_parser()
    options = parser.parse_args()

    url = options.url
    external = options.external
    internal = options.internal
    newline = BYTES_NEWLINES[options.newline]
    no_redirects = options.no_redirects
    print_all = options.print_all
    quiet = options.quiet
    threads = options.threads

    # validate threads number:
    if threads < 1:
        errln('the number of threads must be positive.')
        sys.exit(1)

    # 0 means no timeout:
    if options.timeout > 0:
        timeout = options.timeout
    else:
        timeout = None

    allow_redirects = not(no_redirects)
    run(url, allow_redirects, internal, external, newline, print_all, quiet, threads, timeout)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
