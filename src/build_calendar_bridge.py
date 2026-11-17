"""Build a local signed EventKit app; never modifies the Calendar database."""
from pathlib import Path
import argparse
import os
import plistlib
import subprocess

root = Path(__file__).resolve().parent
app = Path(os.environ.get('PERSONAL_AGENT_APPS_DIR',Path.home()/'Applications')) / 'Personal Agent Calendar OSS.app'
contents = app/'Contents'
binary = contents/'MacOS/calendar-bridge'
parser=argparse.ArgumentParser()
parser.add_argument('--rebuild',action='store_true',help='Re-sign an existing app; macOS may require calendar permission again')
args=parser.parse_args()
if binary.exists() and not args.rebuild:
    raise SystemExit('Calendar app already exists. Use --rebuild only when intentionally updating its signed code.')
binary.parent.mkdir(parents=True, exist_ok=True)
info = {
    'CFBundleIdentifier': 'org.personalagent.oss.calendar',
    'CFBundleName': 'Personal Agent Calendar',
    'CFBundleDisplayName': 'Personal Agent Calendar',
    'CFBundleExecutable': 'calendar-bridge',
    'CFBundlePackageType': 'APPL',
    'CFBundleShortVersionString': '1.0',
    'CFBundleVersion': '1',
    'LSMinimumSystemVersion': '14.0',
    'LSUIElement': True,
    'NSCalendarsFullAccessUsageDescription':
        '读取日历事件的标题、时间和地点，供你的个人生活助手回答日程问题和安排提醒。程序不会修改日历。',
    'NSCalendarsUsageDescription':
        '读取近期日程供个人生活助手使用，不修改日历。',
}
with (contents/'Info.plist').open('wb') as file:
    plistlib.dump(info, file)
subprocess.run(['/usr/bin/clang', '-O2', '-fobjc-arc', '-mmacosx-version-min=14.0',
                '-framework', 'EventKit', '-framework', 'AppKit',
                str(root/'calendar_bridge.m'), '-o', str(binary)], check=True)
subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-',
                '--identifier', info['CFBundleIdentifier'], str(app)], check=True)
print('Built:', app)
print('Launch the .app normally to authorize. Its local snapshot is the runtime permission/status source.')
