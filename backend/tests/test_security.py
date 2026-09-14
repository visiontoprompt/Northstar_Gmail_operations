from app.security import generate_totp, normalize_totp_secret


def test_totp_matches_rfc6238_style_vector():
    assert generate_totp("JBSWY3DPEHPK3PXP", at=1609459200) == "141010"
    assert generate_totp("JBSWY3DPEHPK3PXP", at=1700000000) == "324550"


def test_totp_accepts_messy_secret_encodings():
    assert generate_totp("jbsw y3dp-ehp k3pxp", at=1609459200) == "141010"
    assert normalize_totp_secret("jb sw y3d peh pk3 pxp") == normalize_totp_secret("JBSWY3DPEHPK3PXP")