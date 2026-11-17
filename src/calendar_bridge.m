#import <AppKit/AppKit.h>
#import <EventKit/EventKit.h>
#import <Foundation/Foundation.h>
#include <sys/stat.h>

// Native EventKit read API only. No private database or account credential use.
static EKEventStore *store;
static NSURL *baseURL;
static NSISO8601DateFormatter *iso;
static NSTimeZone *sg;

static NSString *statusName(void) {
    switch ([EKEventStore authorizationStatusForEntityType:EKEntityTypeEvent]) {
        case EKAuthorizationStatusNotDetermined: return @"not_determined";
        case EKAuthorizationStatusRestricted: return @"restricted";
        case EKAuthorizationStatusDenied: return @"denied";
        case EKAuthorizationStatusFullAccess: return @"authorized";
        case EKAuthorizationStatusWriteOnly: return @"write_only";
        default: return @"unknown";
    }
}

static void writeSnapshot(NSDictionary *object) {
    NSError *error = nil;
    [[NSFileManager defaultManager] createDirectoryAtURL:baseURL
        withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:&error];
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingSortedKeys error:&error];
    NSURL *url = [baseURL URLByAppendingPathComponent:@"calendar-snapshot.json"];
    if (!data || ![data writeToURL:url options:NSDataWritingAtomic error:&error]) {
        NSLog(@"Calendar snapshot error: %@", error.localizedDescription);
        return;
    }
    chmod(url.fileSystemRepresentation,0600);
}

static void refresh(void) {
    NSDate *now = [NSDate date];
    NSString *status = statusName();
    NSMutableDictionary *result = [@{@"status":status, @"captured_at":[iso stringFromDate:now],
        @"timezone":sg.name, @"read_only":@YES, @"events":@[]} mutableCopy];
    if (![status isEqualToString:@"authorized"]) {
        writeSnapshot(result);
        return;
    }
    NSCalendar *calendar = [[NSCalendar alloc] initWithCalendarIdentifier:NSCalendarIdentifierGregorian];
    calendar.timeZone = sg;
    NSDate *start = [calendar startOfDayForDate:[calendar dateByAddingUnit:NSCalendarUnitDay value:-7 toDate:now options:0]];
    NSDate *end = [calendar startOfDayForDate:[calendar dateByAddingUnit:NSCalendarUnitDay value:91 toDate:now options:0]];
    NSArray<EKCalendar *> *calendars = [store calendarsForEntityType:EKEntityTypeEvent];
    NSPredicate *predicate = [store predicateForEventsWithStartDate:start endDate:end calendars:calendars];
    NSMutableArray<EKEvent *> *matches = [NSMutableArray array];
    for (EKEvent *event in [store eventsMatchingPredicate:predicate]) {
        if (event.status != EKEventStatusCanceled) [matches addObject:event];
    }
    [matches sortUsingComparator:^NSComparisonResult(EKEvent *a, EKEvent *b) { return [a.startDate compare:b.startDate]; }];
    NSMutableArray *events = [NSMutableArray array];
    for (EKEvent *event in matches) {
        if (events.count >= 2000) break;
        NSString *availability = @"unknown";
        switch (event.availability) {
            case EKEventAvailabilityFree: availability=@"free";break;
            case EKEventAvailabilityBusy: availability=@"busy";break;
            case EKEventAvailabilityTentative: availability=@"tentative";break;
            case EKEventAvailabilityUnavailable: availability=@"unavailable";break;
            default: break;
        }
        [events addObject:@{@"title":event.title ?: @"未命名事件",
            @"start":[iso stringFromDate:event.startDate], @"end":[iso stringFromDate:event.endDate],
            @"all_day":@(event.allDay), @"availability":availability,
            @"location":event.location ?: @"", @"calendar":event.calendar.title ?: @""}];
    }
    result[@"coverage_start"]=[iso stringFromDate:start];
    result[@"coverage_end"]=[iso stringFromDate:end];
    result[@"calendar_count"]=@(calendars.count);
    result[@"event_count"]=@(matches.count);
    result[@"truncated"]=@(matches.count>2000);
    result[@"events"]=events;
    writeSnapshot(result);
}

@interface CalendarDelegate : NSObject <NSApplicationDelegate>
@property(strong) NSTimer *timer;
@property(strong) id observer;
@end
@implementation CalendarDelegate
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    if ([statusName() isEqualToString:@"not_determined"]) {
        [store requestFullAccessToEventsWithCompletion:^(BOOL granted, NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{ refresh(); });
        }];
    } else {
        refresh();
    }
    self.timer = [NSTimer scheduledTimerWithTimeInterval:60 repeats:YES block:^(NSTimer *timer) { refresh(); }];
    self.observer = [[NSNotificationCenter defaultCenter] addObserverForName:EKEventStoreChangedNotification
        object:store queue:[NSOperationQueue mainQueue] usingBlock:^(NSNotification *note) { refresh(); }];
}
@end
static CalendarDelegate *delegate;

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        umask(0077);
        sg = [NSTimeZone timeZoneWithName:(NSProcessInfo.processInfo.environment[@"PERSONAL_AGENT_TIMEZONE"] ?: @"UTC")];
        iso = [NSISO8601DateFormatter new]; iso.timeZone=sg;
        baseURL = [[[NSFileManager defaultManager] homeDirectoryForCurrentUser]
            URLByAppendingPathComponent:@".local/share/personal-agent-oss/data"];
        NSString *custom=NSProcessInfo.processInfo.environment[@"PERSONAL_AGENT_DATA_DIR"];
        if(custom.length)baseURL=[NSURL fileURLWithPath:custom isDirectory:YES];
        store = [EKEventStore new];
        if ([[NSProcessInfo processInfo].arguments containsObject:@"--status"]) {
            printf("{\"status\":\"%s\"}\n",statusName().UTF8String);
            return 0;
        }
        NSApplication *application=[NSApplication sharedApplication];
        [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
        delegate=[CalendarDelegate new]; application.delegate=delegate;
        [application run];
    }
    return 0;
}
