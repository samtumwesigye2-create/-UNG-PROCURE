import pytest
from supplier_profiles import validate_supplier_profile


def test_supplier_profile_rejects_negative_lead_time():
    with pytest.raises(ValueError, match='lead_time_days_cannot_be_negative'):
        validate_supplier_profile('SUP-001', 'USD', -1, 'approved')


def test_supplier_profile_rejects_invalid_status():
    with pytest.raises(ValueError, match='invalid_supplier_status'):
        validate_supplier_profile('SUP-001', 'USD', 5, 'unknown')
