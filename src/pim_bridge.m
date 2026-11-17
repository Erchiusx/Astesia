#import <AppKit/AppKit.h>
#import <EventKit/EventKit.h>
#include <sys/stat.h>
#include <math.h>
static EKEventStore *store;
static NSURL *root;
static NSISO8601DateFormatter *iso;
static BOOL busy=NO;
static id fail(NSString *message){@throw [NSException exceptionWithName:@"PIMError" reason:message userInfo:nil];}
static NSString *text(id x){return [x isKindOfClass:NSString.class]?x:@"";}
static NSString *required(NSDictionary *p,NSString *key){NSString *s=text(p[key]);if(!s.length)return fail([@"Missing " stringByAppendingString:key]);return s;}
static NSDate *date(id value){
 NSString *s=text(value);NSDate *d=[iso dateFromString:s];
 if(!d){NSISO8601DateFormatter *f=[NSISO8601DateFormatter new];f.formatOptions=NSISO8601DateFormatWithInternetDateTime|NSISO8601DateFormatWithFractionalSeconds;d=[f dateFromString:s];}
 if(!d && s.length==10){NSDateFormatter *f=[NSDateFormatter new];f.locale=[NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"];f.timeZone=[NSTimeZone timeZoneWithName:(NSProcessInfo.processInfo.environment[@"PERSONAL_AGENT_TIMEZONE"] ?: @"UTC")];f.dateFormat=@"yyyy-MM-dd";f.lenient=NO;d=[f dateFromString:s];}
 if(!d)return fail(@"Date must be YYYY-MM-DD or ISO8601 with timezone");return d;
}
static NSString *stamp(NSDate *d){return d?[iso stringFromDate:d]:@"";}
static BOOL spin(BOOL(^done)(void),NSTimeInterval seconds){NSDate *end=[NSDate dateWithTimeIntervalSinceNow:seconds];while(!done() && end.timeIntervalSinceNow>0)[NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:.05]];return done();}
static NSString *authName(EKEntityType kind){switch([EKEventStore authorizationStatusForEntityType:kind]){case EKAuthorizationStatusFullAccess:return @"authorized";case EKAuthorizationStatusNotDetermined:return @"not_determined";case EKAuthorizationStatusDenied:return @"denied";case EKAuthorizationStatusRestricted:return @"restricted";case EKAuthorizationStatusWriteOnly:return @"write_only";default:return @"unknown";}}
static void authorize(EKEntityType kind){
 if([authName(kind) isEqual:@"not_determined"]){__block BOOL done=NO;void(^cb)(BOOL,NSError*)=^(BOOL granted,NSError *e){dispatch_async(dispatch_get_main_queue(),^{done=YES;});};if(kind==EKEntityTypeEvent)[store requestFullAccessToEventsWithCompletion:cb];else[store requestFullAccessToRemindersWithCompletion:cb];if(!spin(^BOOL{return done;},120))fail(@"Permission request timed out");}
 if(![authName(kind) isEqual:@"authorized"])fail(kind==EKEntityTypeEvent?@"calendar_permission_denied":@"reminders_permission_denied");
}
static NSDictionary *calInfo(EKCalendar *c){return @{@"id":c.calendarIdentifier?:@"",@"title":c.title?:@"",@"source":c.source.title?:@"",@"writable":@(c.allowsContentModifications)};}
static EKCalendar *targetCalendar(NSDictionary *p,EKEntityType kind){NSString *cid=text(p[kind==EKEntityTypeEvent?@"calendar_id":@"list_id"]);EKCalendar *c=cid.length?[store calendarWithIdentifier:cid]:(kind==EKEntityTypeEvent?store.defaultCalendarForNewEvents:store.defaultCalendarForNewReminders);if(!c||![store calendarsForEntityType:kind]||![[store calendarsForEntityType:kind] containsObject:c])return fail(@"Target calendar/list not found");if(!c.allowsContentModifications)return fail(@"Target calendar/list is read-only");return c;}
static void guardVersion(NSDictionary *p,NSDate *modified){if(![p[@"expected_modified"] isKindOfClass:NSNumber.class])fail(@"Read the item first and supply expected_modified");if(fabs([p[@"expected_modified"] doubleValue]-(modified?modified.timeIntervalSince1970:0))>.001)fail(@"Item changed since it was read; read it again");}
static NSDictionary *eventInfo(EKEvent *e){return @{@"id":e.eventIdentifier?:@"",@"calendar_id":e.calendar.calendarIdentifier?:@"",@"calendar":e.calendar.title?:@"",@"writable":@(e.calendar.allowsContentModifications),@"title":e.title?:@"",@"start":stamp(e.startDate),@"end":stamp(e.endDate),@"all_day":@(e.allDay),@"location":e.location?:@"",@"notes":e.notes?:@"",@"recurring":@(e.hasRecurrenceRules),@"modified":@(e.lastModifiedDate?e.lastModifiedDate.timeIntervalSince1970:0)};}
static void eventFields(EKEvent *e,NSDictionary *p,BOOL creating){
 if(creating||p[@"title"])e.title=required(p,@"title");
 if(creating||p[@"start"])e.startDate=date(p[@"start"]);
 if(creating||p[@"end"])e.endDate=date(p[@"end"]);
 if([e.startDate compare:e.endDate]!=NSOrderedAscending)fail(@"End must be after start");
 if(p[@"all_day"])e.allDay=[p[@"all_day"] boolValue];
 if(p[@"location"])e.location=text(p[@"location"]);
 if(p[@"notes"])e.notes=text(p[@"notes"]);
 if(p[@"calendar_id"]||creating)e.calendar=targetCalendar(p,EKEntityTypeEvent);
 if(p[@"alarm_minutes_before"]){id a=p[@"alarm_minutes_before"];if(a==NSNull.null)e.alarms=@[];else{if(![a isKindOfClass:NSNumber.class]||[a doubleValue]<0||[a doubleValue]>10080)fail(@"Invalid alarm_minutes_before");e.alarms=@[[EKAlarm alarmWithRelativeOffset:-60*[a doubleValue]]];}}
}
static id calendarAction(NSString *action,NSDictionary *p){
 authorize(EKEntityTypeEvent);NSError *err=nil;
 if([action isEqual:@"calendars"]){NSMutableArray *a=[NSMutableArray new];for(EKCalendar *c in [store calendarsForEntityType:EKEntityTypeEvent])[a addObject:calInfo(c)];return @{@"calendars":a,@"default_id":store.defaultCalendarForNewEvents.calendarIdentifier?:@""};}
 if([action isEqual:@"list"]){NSDate *start=date(required(p,@"start")),*end=date(required(p,@"end"));if([start compare:end]!=NSOrderedAscending||[end timeIntervalSinceDate:start]>366*86400)fail(@"Query range must be positive and at most 366 days");EKCalendar *selected=p[@"calendar_id"]?[store calendarWithIdentifier:required(p,@"calendar_id")]:nil;if(p[@"calendar_id"]&&!selected)fail(@"Calendar not found");NSArray *cals=selected?@[selected]:nil;NSArray *events=[store eventsMatchingPredicate:[store predicateForEventsWithStartDate:start endDate:end calendars:cals]];events=[events sortedArrayUsingComparator:^NSComparisonResult(EKEvent *a,EKEvent *b){return [a.startDate compare:b.startDate];}];NSMutableArray *out=[NSMutableArray new];NSString *q=text(p[@"query"]);BOOL more=NO;for(EKEvent *e in events){if(e.status==EKEventStatusCanceled)continue;if(q.length&&[[(e.title?:@"") stringByAppendingString:e.location?:@""] rangeOfString:q options:NSCaseInsensitiveSearch].location==NSNotFound)continue;if(out.count>=100){more=YES;break;}[out addObject:eventInfo(e)];}return @{@"events":out,@"truncated":@(more)};}
 EKEvent *e=[action isEqual:@"create"]?[EKEvent eventWithEventStore:store]:[store eventWithIdentifier:required(p,@"id")];if(!e)return fail(@"Event not found");
 if([action isEqual:@"get"])return eventInfo(e);
 if(![action isEqual:@"create"]){if(!e.calendar.allowsContentModifications)fail(@"Calendar is read-only");guardVersion(p,e.lastModifiedDate);if(e.attendees.count)fail(@"Events with attendees require Calendar app to avoid unintended invitation messages");if(e.hasRecurrenceRules)fail(@"Recurring event mutation is not supported; use Calendar to choose occurrence/series scope");}
 if([action isEqual:@"delete"]){NSDictionary *old=eventInfo(e);if(![store removeEvent:e span:EKSpanThisEvent commit:YES error:&err])return fail(err.localizedDescription);return @{@"deleted":@YES,@"item":old};}
 if(![action isEqual:@"create"]&&![action isEqual:@"update"])return fail(@"Unknown calendar action");
 eventFields(e,p,[action isEqual:@"create"]);if(![store saveEvent:e span:EKSpanThisEvent commit:YES error:&err])return fail(err.localizedDescription);EKEvent *saved=[store eventWithIdentifier:e.eventIdentifier];if(!saved)return fail(@"Saved event could not be read back");return eventInfo(saved);
}
static NSDictionary *reminderInfo(EKReminder *r){NSDateComponents *dc=r.dueDateComponents;NSDate *due=nil;if(dc){NSCalendar *c=dc.calendar?:[NSCalendar calendarWithIdentifier:NSCalendarIdentifierGregorian];c.timeZone=dc.timeZone?:[NSTimeZone timeZoneWithName:(NSProcessInfo.processInfo.environment[@"PERSONAL_AGENT_TIMEZONE"] ?: @"UTC")];due=[c dateFromComponents:dc];}return @{@"id":r.calendarItemIdentifier?:@"",@"list_id":r.calendar.calendarIdentifier?:@"",@"list":r.calendar.title?:@"",@"writable":@(r.calendar.allowsContentModifications),@"title":r.title?:@"",@"notes":r.notes?:@"",@"completed":@(r.completed),@"due":stamp(due),@"due_has_time":@(dc&&dc.hour!=NSDateComponentUndefined),@"recurring":@(r.hasRecurrenceRules),@"modified":@(r.lastModifiedDate?r.lastModifiedDate.timeIntervalSince1970:0)};}
static id reminderAction(NSString *action,NSDictionary *p){
 authorize(EKEntityTypeReminder);NSError *err=nil;
 if([action isEqual:@"lists"]){NSMutableArray *a=[NSMutableArray new];for(EKCalendar *c in [store calendarsForEntityType:EKEntityTypeReminder])[a addObject:calInfo(c)];return @{@"lists":a,@"default_id":store.defaultCalendarForNewReminders.calendarIdentifier?:@""};}
 if([action isEqual:@"list"]){__block NSArray *results=nil;__block BOOL done=NO;EKCalendar *selected=p[@"list_id"]?[store calendarWithIdentifier:required(p,@"list_id")]:nil;if(p[@"list_id"]&&!selected)fail(@"List not found");NSArray *cals=selected?@[selected]:nil;NSPredicate *pred=[p[@"include_completed"] boolValue]?[store predicateForRemindersInCalendars:cals]:[store predicateForIncompleteRemindersWithDueDateStarting:nil ending:nil calendars:cals];[store fetchRemindersMatchingPredicate:pred completion:^(NSArray *a){dispatch_async(dispatch_get_main_queue(),^{results=a;done=YES;});}];if(!spin(^BOOL{return done;},30))fail(@"Reminder query timed out");NSMutableArray *out=[NSMutableArray new];NSString *q=text(p[@"query"]);BOOL more=NO;for(EKReminder *r in results){if(q.length&&[(r.title?:@"") rangeOfString:q options:NSCaseInsensitiveSearch].location==NSNotFound)continue;if(out.count>=100){more=YES;break;}[out addObject:reminderInfo(r)];}return @{@"reminders":out,@"truncated":@(more)};}
 BOOL create=[action isEqual:@"create"];EKReminder *r=create?[EKReminder reminderWithEventStore:store]:(EKReminder*)[store calendarItemWithIdentifier:required(p,@"id")];if(![r isKindOfClass:EKReminder.class])return fail(@"Reminder not found");if([action isEqual:@"get"])return reminderInfo(r);
 if(!create){if(!r.calendar.allowsContentModifications)fail(@"List is read-only");guardVersion(p,r.lastModifiedDate);if(r.hasRecurrenceRules)fail(@"Recurring reminder mutation is not supported");}
 if([action isEqual:@"delete"]){NSDictionary *old=reminderInfo(r);if(![store removeReminder:r commit:YES error:&err])return fail(err.localizedDescription);return @{@"deleted":@YES,@"item":old};}
 if(!create&&![action isEqual:@"update"])return fail(@"Unknown reminder action");
 if(create||p[@"title"])r.title=required(p,@"title");if(create||p[@"list_id"])r.calendar=targetCalendar(p,EKEntityTypeReminder);if(p[@"notes"])r.notes=text(p[@"notes"]);if(p[@"completed"])r.completed=[p[@"completed"] boolValue];
 if(p[@"due"]){if(p[@"due"]==NSNull.null){r.dueDateComponents=nil;r.alarms=@[];}else{NSString *s=required(p,@"due");NSDate *d=date(s);NSCalendar *c=[NSCalendar calendarWithIdentifier:NSCalendarIdentifierGregorian];c.timeZone=[NSTimeZone timeZoneWithName:(NSProcessInfo.processInfo.environment[@"PERSONAL_AGENT_TIMEZONE"] ?: @"UTC")];NSCalendarUnit u=NSCalendarUnitYear|NSCalendarUnitMonth|NSCalendarUnitDay;if(s.length>10)u|=NSCalendarUnitHour|NSCalendarUnitMinute;NSDateComponents *dc=[c components:u fromDate:d];dc.calendar=c;dc.timeZone=c.timeZone;r.dueDateComponents=dc;r.alarms=s.length>10?@[[EKAlarm alarmWithAbsoluteDate:d]]:@[];}}
 if(![store saveReminder:r commit:YES error:&err])return fail(err.localizedDescription);EKReminder *saved=(EKReminder*)[store calendarItemWithIdentifier:r.calendarItemIdentifier];if(![saved isKindOfClass:EKReminder.class])return fail(@"Saved reminder could not be read back");return reminderInfo(saved);
}
static NSString *quote(NSString *s){NSMutableString *q=[text(s) mutableCopy];[q replaceOccurrencesOfString:@"\\" withString:@"\\\\" options:0 range:NSMakeRange(0,q.length)];[q replaceOccurrencesOfString:@"\"" withString:@"\\\"" options:0 range:NSMakeRange(0,q.length)];[q replaceOccurrencesOfString:@"\r" withString:@"\\r" options:0 range:NSMakeRange(0,q.length)];[q replaceOccurrencesOfString:@"\n" withString:@"\\n" options:0 range:NSMakeRange(0,q.length)];return [NSString stringWithFormat:@"\"%@\"",q];}
static NSString *html(NSString *s){NSMutableString *q=[text(s) mutableCopy];for(NSArray *pair in @[@[@"&",@"&amp;"],@[@"<",@"&lt;"],@[@">",@"&gt;"],@[@"\n",@"<br>"]])[q replaceOccurrencesOfString:pair[0] withString:pair[1] options:0 range:NSMakeRange(0,q.length)];return q;}
static NSAppleEventDescriptor *script(NSString *body){NSDictionary *err=nil;NSString *source=[NSString stringWithFormat:@"with timeout of 60 seconds\ntell application \"Notes\"\n%@\nend tell\nend timeout",body];NSAppleEventDescriptor *out=[[[NSAppleScript alloc] initWithSource:source] executeAndReturnError:&err];if(!out)fail([NSString stringWithFormat:@"notes_automation_error %@: %@",err[NSAppleScriptErrorNumber],err[NSAppleScriptErrorMessage]]);return out;}
static NSDictionary *noteInfo(NSString *nid){NSAppleEventDescriptor *d=script([NSString stringWithFormat:@"set n to note id %@\nif password protected of n then error \"Locked note is not accessible\"\nset fid to \"\"\ntry\nset fid to id of container of n\nend try\nreturn {id of n, name of n, plaintext of n, modification date of n, fid, count of attachments of n}",quote(nid)]);NSString *body=[d descriptorAtIndex:3].stringValue?:@"";BOOL truncated=body.length>20000;if(truncated)body=[body substringToIndex:20000];return @{@"id":[d descriptorAtIndex:1].stringValue?:@"",@"title":[d descriptorAtIndex:2].stringValue?:@"",@"body":body,@"body_truncated":@(truncated),@"modified":@([d descriptorAtIndex:4].dateValue.timeIntervalSince1970),@"folder_id":[d descriptorAtIndex:5].stringValue?:@"",@"attachment_count":@([d descriptorAtIndex:6].int32Value)};}
static id notesAction(NSString *action,NSDictionary *p){
 if([action isEqual:@"folders"]){NSAppleEventDescriptor *d=script(@"set rows to {}\nrepeat with a in accounts\nrepeat with f in folders of a\nset end of rows to {id of f, name of f, name of a}\nend repeat\nend repeat\nreturn {id of default folder of default account, rows}");NSMutableArray *a=[NSMutableArray new];NSAppleEventDescriptor *rows=[d descriptorAtIndex:2];for(NSInteger i=1;i<=rows.numberOfItems;i++){NSAppleEventDescriptor *v=[rows descriptorAtIndex:i];[a addObject:@{@"id":[v descriptorAtIndex:1].stringValue?:@"",@"title":[v descriptorAtIndex:2].stringValue?:@"",@"account":[v descriptorAtIndex:3].stringValue?:@""}];}return @{@"folders":a,@"default_id":[d descriptorAtIndex:1].stringValue?:@""};}
 if([action isEqual:@"list"]){NSString *where=text(p[@"folder_id"]).length?[NSString stringWithFormat:@"notes of folder id %@",quote(p[@"folder_id"])]:@"notes";NSString *q=text(p[@"query"]);NSString *selection=q.length?[NSString stringWithFormat:@"(%@ whose name contains %@)",where,quote(q)]:where;NSAppleEventDescriptor *d=script([NSString stringWithFormat:@"set rows to {}\nset matches to %@\nrepeat with n in matches\nif (count rows) >= 100 then exit repeat\nset end of rows to {id of n, name of n, modification date of n, password protected of n}\nend repeat\nreturn {rows, (count matches) > 100}",selection]);NSMutableArray *a=[NSMutableArray new];NSAppleEventDescriptor *rows=[d descriptorAtIndex:1];for(NSInteger i=1;i<=rows.numberOfItems;i++){NSAppleEventDescriptor *v=[rows descriptorAtIndex:i];[a addObject:@{@"id":[v descriptorAtIndex:1].stringValue?:@"",@"title":[v descriptorAtIndex:2].stringValue?:@"",@"modified":@([v descriptorAtIndex:3].dateValue.timeIntervalSince1970),@"locked":@([v descriptorAtIndex:4].booleanValue)}];}return @{@"notes":a,@"truncated":@([d descriptorAtIndex:2].booleanValue),@"query_mode":@"title"};}
 if([action isEqual:@"create"]){NSString *title=required(p,@"title");NSString *body=[NSString stringWithFormat:@"<div><b>%@</b></div><div>%@</div>",html(title),html(text(p[@"body"]))];NSString *target=text(p[@"folder_id"]).length?[NSString stringWithFormat:@"folder id %@",quote(p[@"folder_id"])]:@"default folder of default account";NSString *nid=script([NSString stringWithFormat:@"set n to make new note at %@ with properties {body:%@}\nreturn id of n",target,quote(body)]).stringValue;@try{return noteInfo(nid);}@catch(NSException *e){return @{@"id":nid?:@"",@"created":@YES,@"verification_error":e.reason?:@"Readback failed"};}}
 NSString *nid=required(p,@"id");NSDictionary *old=noteInfo(nid);if([action isEqual:@"get"])return old;guardVersion(p,[NSDate dateWithTimeIntervalSince1970:[old[@"modified"] doubleValue]]);
 if([action isEqual:@"delete"]){script([NSString stringWithFormat:@"delete note id %@\nreturn true",quote(nid)]);return @{@"deleted":@YES,@"item":old};}
 if(![action isEqual:@"update"])return fail(@"Unknown notes action");
 NSString *mode=text(p[@"mode"]);if(!mode.length)mode=@"append";if(![mode isEqual:@"append"]&&![mode isEqual:@"replace"])fail(@"Notes update mode must be append or replace");if([mode isEqual:@"replace"]&&[old[@"body_truncated"] boolValue])fail(@"Cannot replace a note based on truncated content");if([mode isEqual:@"replace"]&&[old[@"attachment_count"] integerValue]>0)fail(@"Cannot replace a note containing attachments; append preserves them");
 NSMutableString *body=[[NSString stringWithFormat:@"set n to note id %@\n",quote(nid)] mutableCopy];
 if(p[@"body"]){if([mode isEqual:@"append"])[body appendFormat:@"set body of n to (body of n) & %@\n",quote([NSString stringWithFormat:@"<div>%@</div>",html(text(p[@"body"]))])];else[body appendFormat:@"set body of n to %@\n",quote([NSString stringWithFormat:@"<div><b>%@</b></div><div>%@</div>",html(text(p[@"title"]).length?p[@"title"]:old[@"title"]),html(text(p[@"body"]))])];}
 if(p[@"title"])[body appendFormat:@"set name of n to %@\n",quote(required(p,@"title"))];[body appendString:@"return id of n"];script(body);return noteInfo(nid);
}
static void saveJSON(NSURL *path,id obj){NSError *e=nil;NSData *d=[NSJSONSerialization dataWithJSONObject:obj options:NSJSONWritingSortedKeys error:&e];if(!d||![d writeToURL:path options:NSDataWritingAtomic error:&e])fail(e.localizedDescription?:@"Could not persist result");chmod(path.fileSystemRepresentation,0600);}
#include "speech_notes_bridge.inc.m"
static BOOL mutation(NSString *action){return [@[@"create",@"update",@"delete"] containsObject:action];}
static void poll(void){
 if(busy)return;busy=YES;
 @try{
  NSArray<NSURL*> *files=[[NSFileManager defaultManager] contentsOfDirectoryAtURL:root includingPropertiesForKeys:nil options:0 error:nil];
  for(NSURL *path in files){if(![path.lastPathComponent hasSuffix:@".request.json"])continue;
   NSString *stem=[path.lastPathComponent substringToIndex:path.lastPathComponent.length-13];NSUUID *uuid=[[NSUUID alloc] initWithUUIDString:stem];if(!uuid)continue;
   NSURL *result=[[root URLByAppendingPathComponent:@"responses"] URLByAppendingPathComponent:[stem stringByAppendingString:@".json"]];if([[NSFileManager defaultManager] fileExistsAtPath:result.path])continue;
   NSDictionary *response=nil;
   @try{
    NSDictionary *attrs=[[NSFileManager defaultManager] attributesOfItemAtPath:path.path error:nil];if(![attrs[NSFileType] isEqual:NSFileTypeRegular]||[attrs[NSFileSize] unsignedLongLongValue]>65536)fail(@"Invalid request file");
    NSDictionary *request=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfURL:path] options:0 error:nil];if(![request isKindOfClass:NSDictionary.class])fail(@"Invalid request JSON");NSString *domain=required(request,@"domain"),*action=required(request,@"action");NSDictionary *params=request[@"params"]?:@{};
    if(mutation(action)){
     NSURL *ctx=[[[root URLByDeletingLastPathComponent] URLByAppendingPathComponent:@"pim-task.json"] URLByStandardizingPath];NSDictionary *task=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfURL:ctx] options:0 error:nil];NSString *evidence=text(request[@"evidence"]);
     if(![task[@"kind"] isEqual:@"ask"]||![task[@"job_id"] isEqual:request[@"job_id"]]||!evidence.length||[text(task[@"question"]) rangeOfString:evidence].location==NSNotFound||NSDate.date.timeIntervalSince1970-[task[@"created"] doubleValue]>900)fail(@"Write requires current user task and exact intent evidence");
    }
    id value=nil;
    if([domain isEqual:@"system"]&&[action isEqual:@"status"])value=@{@"calendar":authName(EKEntityTypeEvent),@"reminders":authName(EKEntityTypeReminder),@"notes":@"automation_permission_checked_on_use"};
    else if([domain isEqual:@"calendar"])value=calendarAction(action,params);
    else if([domain isEqual:@"reminders"])value=reminderAction(action,params);
    else if([domain isEqual:@"notes"])value=notesAction(action,params);
    else fail(@"Unknown domain/action");
    response=@{@"ok":@YES,@"request_id":stem,@"domain":domain,@"action":action,@"data":value?:@{}};
   }@catch(NSException *e){response=@{@"ok":@NO,@"request_id":stem,@"error":e.reason?:@"Unknown error"};}
   saveJSON(result,response);
   NSURL *history=[[root URLByAppendingPathComponent:@"history"] URLByAppendingPathComponent:path.lastPathComponent];[[NSFileManager defaultManager] moveItemAtURL:path toURL:history error:nil];
  }
  pollSpeechNotes();
 }@catch(NSException *e){NSLog(@"PIM bridge error: %@",e.reason);}
 busy=NO;
}
@interface PIMDelegate:NSObject<NSApplicationDelegate>
@property(strong) NSTimer *timer;
@end
@implementation PIMDelegate
-(void)applicationDidFinishLaunching:(NSNotification*)n{self.timer=[NSTimer scheduledTimerWithTimeInterval:.15 repeats:YES block:^(NSTimer*t){poll();}];poll();}
@end
int main(int argc,const char *argv[]){@autoreleasepool{umask(0077);iso=[NSISO8601DateFormatter new];iso.timeZone=[NSTimeZone timeZoneWithName:(NSProcessInfo.processInfo.environment[@"PERSONAL_AGENT_TIMEZONE"] ?: @"UTC")];store=[EKEventStore new];root=[[[NSFileManager.defaultManager homeDirectoryForCurrentUser] URLByAppendingPathComponent:@".local/share/personal-agent-oss/data/workspace/pim-requests"] URLByStandardizingPath];NSString *custom=NSProcessInfo.processInfo.environment[@"PERSONAL_AGENT_DATA_DIR"];if(custom.length)root=[[NSURL fileURLWithPath:custom isDirectory:YES] URLByAppendingPathComponent:@"workspace/pim-requests"];[[NSFileManager defaultManager] createDirectoryAtURL:root withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:nil];for(NSString *folder in @[@"responses",@"history"])[[NSFileManager defaultManager] createDirectoryAtURL:[root URLByAppendingPathComponent:folder] withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:nil];NSApplication *app=NSApplication.sharedApplication;[app setActivationPolicy:NSApplicationActivationPolicyAccessory];PIMDelegate *delegate=[PIMDelegate new];app.delegate=delegate;[app run];}return 0;}
