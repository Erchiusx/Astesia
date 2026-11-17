import json
import unittest
from worker import progress_text


class ProgressTests(unittest.TestCase):
    def event(self,kind,text):
        return json.dumps({'type':'item.completed','item':{'type':kind,'text':text}})

    def test_only_completed_public_text_is_forwarded(self):
        self.assertEqual(progress_text(self.event('agent_message','先核实日历中的时间冲突。')),'先核实日历中的时间冲突。')
        for kind in ['reasoning','command_execution','web_search']:
            self.assertIsNone(progress_text(self.event(kind,'private content')))
        for text in ['{"reply":"最终答案"}','```json\n{}\n```','[1,2]','x'*701,'']:
            self.assertIsNone(progress_text(self.event('agent_message',text)))
        self.assertIsNone(progress_text('not json'))
        self.assertIsNone(progress_text('{"type":"item.started","item":{"type":"agent_message","text":"partial"}}'))


if __name__=='__main__': unittest.main()
