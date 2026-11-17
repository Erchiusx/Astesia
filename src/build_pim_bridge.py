"""Build the signed local EventKit/Notes tool host without touching app databases."""
import pathlib,plistlib,subprocess,os
project=pathlib.Path(__file__).resolve().parent
app=pathlib.Path(os.environ.get('PERSONAL_AGENT_APPS_DIR',pathlib.Path.home()/'Applications'))/'Personal Agent Tools OSS.app';
if app.exists():raise SystemExit('Tools app already exists; remove or back it up explicitly before rebuilding')
contents=app/'Contents';binary=contents/'MacOS/pim-bridge';binary.parent.mkdir(parents=True,exist_ok=True)
info=dict(CFBundleIdentifier='org.personalagent.oss.tools',CFBundleName='Personal Agent Tools',CFBundleDisplayName='Personal Agent Tools',CFBundleExecutable='pim-bridge',CFBundlePackageType='APPL',CFBundleShortVersionString='1.0',CFBundleVersion='1',LSMinimumSystemVersion='14.0',LSUIElement=True,NSCalendarsFullAccessUsageDescription='按用户的请求查询、新增、修改或删除日历事件。只有明确要求的事项才会写入。',NSRemindersFullAccessUsageDescription='按用户的请求查询、新增、修改、完成或删除提醒事项。',NSRemindersUsageDescription='读写用户明确要求的提醒事项。',NSAppleEventsUsageDescription='按用户的请求控制备忘录 App，查询、新增、追加、修改或删除指定备忘录。')
(contents/'Info.plist').write_bytes(plistlib.dumps(info))
subprocess.run(['/usr/bin/clang','-O2','-fobjc-arc','-mmacosx-version-min=14.0','-framework','AppKit','-framework','EventKit',str(project/'pim_bridge.m'),'-o',str(binary)],check=True)
subprocess.run(['/usr/bin/codesign','--force','--sign','-','--identifier',info['CFBundleIdentifier'],str(app)],check=True)
print(app)
