from collectors.web.generic import GenericWeb

class X(GenericWeb):
    source = 'x'
    suffixes = ('site:x.com', 'site:twitter.com') + tuple(
        f'site:x.com {word}' for word in ('幼兒園', '老師', '家長', '體罰', '虐童', '不當管教', '投訴', '申訴', '食安', '退費'))
    domains = ('x.com', 'twitter.com')
