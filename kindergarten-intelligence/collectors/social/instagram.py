from collectors.web.generic import GenericWeb

class Instagram(GenericWeb):
    source = 'instagram'
    suffixes = ('site:instagram.com', 'Instagram')
    domains = ('instagram.com',)
