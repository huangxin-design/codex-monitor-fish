"""User-defined subscription allocation across observed tokens."""
from math import fsum


PRICING = {
    'method': 'subscription_allocation',
    'plan': '20x',
    'weekly_tokens': 2_000_000_000,
    'weeks': 4,
    'subscription_usd': 200,
    'period_tokens': 8_000_000_000,
    'usd_per_million': .025,
    'basis': '按用户设定：20x，20亿Token/周×4周，200美元；'
             '估算分摊，不代表官方固定Token配额或逐次扣费。',
}


def zero_cost(incomplete=False):
    return {'usd': 0.0, 'priced_responses': 0, 'unpriced_responses': 0,
            'incomplete': incomplete}


def add_costs(*costs):
    return {'usd': fsum(c['usd'] for c in costs),
            'priced_responses': sum(c['priced_responses'] for c in costs),
            'unpriced_responses': sum(c['unpriced_responses'] for c in costs),
            'incomplete': any(c['incomplete'] for c in costs)}


def response_cost(usage):
    """Allocate the user-defined subscription rate to one validated response."""
    # Cache is a subset of input, and reasoning is a subset of output.
    usd = usage['total_tokens'] * PRICING['subscription_usd'] / PRICING['period_tokens']
    return {**zero_cost(), 'usd': usd, 'priced_responses': 1}
