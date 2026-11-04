import copy
import unittest

from repo_maintainer.models import SafetyError
from repo_maintainer.pi_edits import verify_materialized_plan


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.original = "# 😀 — ‘quote’   \r\nreturn a - b\n"
        self.files = [{"path":"logic.py", "content":self.original, "before_sha256":"a"*64, "editable":True}]
        self.result = {"evidence":{"edit_protocol":"exact-replacements-v1"},
            "patches":[{"path":"logic.py","replacements":[{"oldText":"a - b","newText":"a + b"}]}],
            "plan":{"edits":[{"path":"logic.py","before_sha256":"a"*64,
                "content":self.original.replace("a - b","a + b")}]}}

    def test_reconstructs_exact_content_including_non_ascii_and_mixed_line_endings(self):
        verify_materialized_plan(self.result,self.files)

    def test_fragment_or_tampered_untouched_bytes_cannot_pass_receipt(self):
        for content in ["return a + b\n",self.result["plan"]["edits"][0]["content"].replace("—","-")]:
            data=copy.deepcopy(self.result);data["plan"]["edits"][0]["content"]=content
            with self.assertRaises(SafetyError): verify_materialized_plan(data,self.files)

    def test_missing_receipt_wrong_hash_readonly_and_unknown_fields_are_rejected(self):
        for mutation in [lambda d:d.pop("patches"),lambda d:d["evidence"].clear(),
                         lambda d:d["plan"]["edits"][0].update(before_sha256="b"*64),
                         lambda d:d["patches"][0].update(content="fragment")]:
            data=copy.deepcopy(self.result);mutation(data)
            with self.assertRaises(SafetyError): verify_materialized_plan(data,self.files)
        with self.assertRaises(SafetyError): verify_materialized_plan(self.result,[{**self.files[0],"editable":False}])

    def test_ambiguous_overlapping_and_missing_preconditions_are_rejected(self):
        for replacements in [[{"oldText":"not here","newText":"x"}],
            [{"oldText":"return a","newText":"x"},{"oldText":"a - b","newText":"y"}],
            [{"oldText":" ","newText":"x"}]]:
            data=copy.deepcopy(self.result);data["patches"][0]["replacements"]=replacements
            with self.assertRaises(SafetyError): verify_materialized_plan(data,self.files)


if __name__ == "__main__": unittest.main()
