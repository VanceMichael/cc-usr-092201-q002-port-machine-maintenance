import unittest

from pmms.core.errors import AuthzError
from pmms.domain import vocab as V

from .support import build_world, new_service, open_mechanical_case


class AuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clk = new_service()
        self.c = build_world(self.svc)
        self.aid, _ = open_mechanical_case(self.svc, self.c, self.clk)

    def test_scoped_session_reads_only_approved_data(self):
        _, token = self.svc.open_diag_session(
            self.aid, "SV-01", purpose="分诊取证",
            data_scopes=[V.DATA_ALARM_RAW, V.DATA_CALIBRATION])
        payload = self.svc.access_diagnostic_data(token, V.DATA_CALIBRATION, "看漂移")
        self.assertTrue(payload["calibrations"])
        with self.assertRaises(AuthzError):
            self.svc.access_diagnostic_data(token, V.DATA_SOFTWARE_PARAMS, "越权")

    def test_every_access_is_logged(self):
        _, token = self.svc.open_diag_session(
            self.aid, "RD-01", purpose="查看报警",
            data_scopes=[V.DATA_ALARM_RAW])
        self.svc.access_diagnostic_data(token, V.DATA_ALARM_RAW, "确认联锁")
        case = self.svc.reg.machine_of_alarm(self.aid).alarms[self.aid]
        sess = case.sessions[-1]
        self.assertEqual(len(sess.accesses), 1)
        self.assertEqual(sess.accesses[0].scope, V.DATA_ALARM_RAW)

    def test_expired_session_token_rejected(self):
        _, token = self.svc.open_diag_session(
            self.aid, "SV-01", purpose="取证",
            data_scopes=[V.DATA_ALARM_RAW], ttl_minutes=5)
        self.clk.advance(6 * 60)
        with self.assertRaises(AuthzError):
            self.svc.access_diagnostic_data(token, V.DATA_ALARM_RAW, "过期取用")

    def test_closed_session_rejected(self):
        sid, token = self.svc.open_diag_session(
            self.aid, "SV-01", purpose="取证", data_scopes=[V.DATA_ALARM_RAW])
        self.svc.close_diag_session(sid, "SV-01")
        with self.assertRaises(AuthzError):
            self.svc.access_diagnostic_data(token, V.DATA_ALARM_RAW, "关闭后取用")

    def test_tampered_token_rejected(self):
        _, token = self.svc.open_diag_session(
            self.aid, "SV-01", purpose="取证", data_scopes=[V.DATA_ALARM_RAW])
        with self.assertRaises(AuthzError):
            self.svc.access_diagnostic_data(token[:-3] + "xxx",
                                            V.DATA_ALARM_RAW, "伪造")

    def test_session_requires_purpose(self):
        with self.assertRaises(AuthzError):
            self.svc.open_diag_session(
                self.aid, "SV-01", purpose="  ",
                data_scopes=[V.DATA_ALARM_RAW])


if __name__ == "__main__":
    unittest.main()
