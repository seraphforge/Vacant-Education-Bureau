from collectors.web.generic import GenericWeb

class Threads(GenericWeb):
    source = 'threads'
    suffixes = ('site:threads.net', 'site:threads.com', 'threads')
    domains = ('threads.net', 'threads.com')
