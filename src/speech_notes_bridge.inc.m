// The dedicated mirror is a standing, narrowly scoped user subscription.
// Its private policy/state sit outside the agent's writable workspace.
static NSDictionary *mirrorJSON(NSURL *url){
 NSData *data=[NSData dataWithContentsOfURL:url];if(!data||data.length>131072)return nil;
 id value=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];return [value isKindOfClass:NSDictionary.class]?value:nil;
}
static void pollSpeechNotes(void){
 static NSTimeInterval nextTry=0;if(NSDate.date.timeIntervalSince1970<nextTry)return;
 NSURL *directory=[[[root URLByDeletingLastPathComponent] URLByDeletingLastPathComponent] URLByAppendingPathComponent:@"speech-notes"];
 NSURL *configURL=[directory URLByAppendingPathComponent:@"config.json"],*statusURL=[directory URLByAppendingPathComponent:@"status.json"];
 NSDictionary *config=mirrorJSON(configURL);if(![config[@"enabled"] boolValue])return;
 NSDictionary *wanted=mirrorJSON([directory URLByAppendingPathComponent:@"desired.json"]);if(!wanted)return;
 NSString *version=text(wanted[@"version"]);NSDictionary *status=mirrorJSON(statusURL);
 if([status[@"ok"] boolValue]&&[status[@"version"] isEqual:version]&&[status[@"note_id"] isEqual:config[@"note_id"]])return;
 nextTry=NSDate.date.timeIntervalSince1970+30;
 @try{
  if([config[@"policy_version"] integerValue]!=1||![[NSUUID alloc] initWithUUIDString:text(config[@"mirror_id"])]||![config[@"mirror_id"] isEqual:wanted[@"mirror_id"]])fail(@"Invalid mirror authorization");
  NSInteger keep=[config[@"keep"] integerValue];NSString *title=required(config,@"title");
  if(keep<1||keep>3||title.length>200||version.length!=64)fail(@"Invalid mirror policy");
  NSArray *entries=wanted[@"entries"];if(![entries isKindOfClass:NSArray.class]||entries.count>keep)fail(@"Invalid mirror entries");
  for(NSDictionary *entry in entries){if(![entry isKindOfClass:NSDictionary.class]||!text(entry[@"id"]).length||!text(entry[@"text"]).length||text(entry[@"text"]).length>10000||![entry[@"created"] isKindOfClass:NSNumber.class])fail(@"Invalid speech entry");}
  NSString *body=required(wanted,@"body");if(body.length>19000)fail(@"Mirror body is too large");
  NSString *nid=text(config[@"note_id"]);
  if(!nid.length){
   if(![config[@"allow_create"] boolValue])fail(@"Mirror target is not provisioned");
   NSDictionary *matches=notesAction(@"list",@{@"query":title});
   for(NSDictionary *n in matches[@"notes"])if([n[@"title"] isEqual:title])fail(@"A note with this title already exists; explicitly select its ID first");
   NSMutableDictionary *params=[@{@"title":title,@"body":body} mutableCopy];if(text(config[@"folder_id"]).length)params[@"folder_id"]=config[@"folder_id"];
   NSDictionary *created=notesAction(@"create",params);nid=required(created,@"id");
   NSMutableDictionary *bound=[config mutableCopy];bound[@"note_id"]=nid;bound[@"allow_create"]=@NO;saveJSON(configURL,bound);config=bound;
  }else{
   // Never write a mirror into a note already in a recovery folder.
   NSDictionary *folders=notesAction(@"folders",@{});
   for(NSDictionary *folder in folders[@"folders"]){
    if(![@[@"recently deleted",@"最近删除",@"最近刪除"] containsObject:[text(folder[@"title"]) lowercaseString]])continue;
    NSDictionary *rows=notesAction(@"list",@{@"folder_id":folder[@"id"],@"query":title});
    for(NSDictionary *n in rows[@"notes"])if([n[@"id"] isEqual:nid])fail(@"Mirror note is in Recently Deleted; restore it explicitly");
   }
   NSDictionary *old=noteInfo(nid);if(![old[@"title"] isEqual:title])fail(@"Mirror note was renamed; review its target before writing");
   notesAction(@"update",@{@"id":nid,@"expected_modified":old[@"modified"],@"title":title,@"body":body,@"mode":@"replace"});
  }
  NSDictionary *verified=noteInfo(nid);if([verified[@"body_truncated"] boolValue]||[verified[@"body"] rangeOfString:body].location==NSNotFound)fail(@"Mirror readback did not match");
  saveJSON(statusURL,@{@"ok":@YES,@"version":version,@"note_id":nid,@"count":@(entries.count),@"updated":@(NSDate.date.timeIntervalSince1970)});
  nextTry=NSDate.date.timeIntervalSince1970+1;
 }@catch(NSException *e){saveJSON(statusURL,@{@"ok":@NO,@"version":version?:@"",@"error":e.reason?:@"Mirror failed",@"updated":@(NSDate.date.timeIntervalSince1970)});}
}
