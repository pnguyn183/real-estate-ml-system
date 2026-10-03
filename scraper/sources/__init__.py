"""Three explicit adapters; there is deliberately no Batdongsan registry entry."""
from .alonhadat import AlonhadatAdapter
from .guland import GulandAdapter
from .homedy import HomedyAdapter

ADAPTERS = {adapter.name: adapter() for adapter in (AlonhadatAdapter, GulandAdapter, HomedyAdapter)}


def get_adapter(name):
    if name not in ADAPTERS:
        raise ValueError("Source must be alonhadat, guland or homedy")
    return ADAPTERS[name]
