import unittest
from recontract import INSTRUCTION, audit_schema, post_validate
class RecontractTest(unittest.TestCase):
 def test_one_based_schema(self):
  item=audit_schema()["properties"]["results"]["items"]["properties"]["selectedIndexes"]["items"]
  self.assertEqual(1,item["minimum"]);self.assertEqual(4,item["maximum"]);self.assertIn("0始まりは禁止",INSTRUCTION)
 def test_post_validate_rejects_zero(self):
  with self.assertRaises(RuntimeError): post_validate({"results":[{"id":"x","selectedIndexes":[0],"explanations":["a"]*4,"questionType":"single"}]},[{"id":"x","choices":[1,2,3,4]}])
if __name__=="__main__":unittest.main()
