"""Industry preset content; the logic that lists, validates and applies them is app/industry_presets.py."""
from .data_a import b2b_saas, real_estate, recruitment
from .data_b import education, healthcare, insurance
from .data_c import agency, generic, manufacturing, retail

BUILDERS = (recruitment, real_estate, b2b_saas, education, insurance, healthcare, agency, manufacturing, retail, generic)
