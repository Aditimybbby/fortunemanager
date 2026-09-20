const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const root=path.resolve(__dirname,'../../fortune/web');
const errors=[];
const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e));
const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{runScripts:'outside-only',url:'https://dashboard.example',virtualConsole:console});
const w=dom.window;w.confirm=()=>true;w.crypto.randomUUID=randomUUID;w.structuredClone=structuredClone;
let config={prefix:'.',reply_delete_after:20,staff_role_id:'111111111111111111',log_channel_id:null,
 greet:{enabled:false,channel_id:null,content:'Welcome {user}!',use_embed:true,title:'Welcome to {server}',description:'You are member #{member_count}',color:'#63d6ac',image:'',autorole_ids:[]},
 ticket:{enabled:false,category_id:null,log_channel_id:null,support_role_ids:[],max_open:1,panels:[]},reaction_roles:[]};
config.embeds={channel_id:null,content:'',title:'An update',description:'Hello',color:'#63d6ac',image:'',fields:[]};
let version=0;let staff=[];const writes=[];
const guild={id:'999999999999999999',name:'Fortune <Leaf>',installed:true,icon:null};
let security={policy:{enabled:false,mode:'observe',punishment:'strip',actor_score:18,guild_score:60,auto_lockdown:false,log_channel_id:null,trust:{},rules:{ban:{enabled:true,count:3,seconds:15,hourly:30}}},version:0,health:{missing_permissions:[],unmanageable_roles:[],workers_running:true},incidents:[],jobs:[]};
let automod={config:{enabled:false,mode:'enforce',log_channel_id:null,timeout_seconds:600,spam_count:6,spam_seconds:5,duplicate_count:3,repeat_count:4,repeat_characters:12,mention_count:5,caps_percent:75,caps_min:12,emoji_count:12,attachment_count:4,escalate_after:5,rules:{spam:{enabled:true,action:'timeout'}},words:[],ban_words:[],allowed_domains:[],blocked_domains:[],blocked_extensions:[],ignored_channels:[],ignored_roles:[],raid:{enabled:false,count:12,seconds:10,min_age_hours:24}},version:0};
w.fetch=async (url,opts={})=>{
 let data={ok:true};let status=200;
 const body=opts.body?JSON.parse(opts.body):{};
 if(opts.method!=='GET'&&opts.method){assert.equal(opts.headers['X-CSRF-Token'],'test-csrf');writes.push([url,body]);}
 if(url==='/api/me')data={user:{id:'222222222222222222',name:'Admin'},csrf:'test-csrf',ready:true,logo:'https://example.com/logo.png',banner:'https://example.com/banner.png'};
 else if(url==='/api/guilds')data={guilds:[guild],invite:'https://discord.com/oauth2/authorize?client_id=1'};
 else if(url.endsWith('/config')){
  if(opts.method==='PUT'){config=body.config;version++;data={config,version,ok:true};}
  else data={config,version,admin:true,guild:{...guild,members:1284},permissions:{kick:'Kick members',mute:'Mute / unmute',ticket_access:'Ticket access'},channels:[{id:'333333333333333333',name:'welcome',type:'text'},{id:'444444444444444444',name:'Support',type:'category'}],roles:[{id:'111111111111111111',name:'Staff'},{id:'555555555555555555',name:'Members'}]};
 } else if(url.endsWith('/tickets'))data={tickets:[]};
 else if(url.endsWith('/staff'))data={staff};
 else if(url.includes('/members?'))data={members:[{id:'666666666666666666',name:'wumpus'}]};
 else if(url.includes('/staff/')){staff=[{user_id:'666666666666666666',name:'wumpus',permissions:body.permissions}];}
 else if(url.endsWith('/security')){if(opts.method==='PUT'){assert.equal(body.code,'owner-code');security.policy=body.policy;security.version++;}data=security;}
 else if(url.endsWith('/automod')){if(opts.method==='PUT'){automod.config=body.config;automod.version++;}data=automod;}
 else if(url.endsWith('/audit'))data={events:[{id:1,actor_id:'222222222222222222',action:'settings.save',detail:'<script>alert(1)</script>',created_at:'2026-09-16T00:00:00Z'}]};
 return {ok:status===200,status,text:async()=>JSON.stringify(data)};
};
w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));
async function until(check){for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setTimeout(r,1));}throw new Error('UI did not reach expected state');}
const query=s=>{const el=w.document.querySelector(s);assert.ok(el,'Missing '+s);return el;};
async function click(s){query(s).click();await new Promise(r=>setTimeout(r,5));}
function input(s,value){const el=query(s);if(el.type==='checkbox')el.checked=value;else el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));}
(async()=>{
 await until(()=>w.document.querySelector('[data-action="guild"]'));
 assert.equal(w.document.querySelector('.guild-card h2').textContent,'Fortune <Leaf>');
 await click('[data-action="guild"]');assert.match(query('h1').textContent,/Server overview/);
 assert.equal(query('.number').textContent,'1,284');
 input('[data-path="reply_delete_after"]','45');
 await click('[data-action="save"]');
 assert.equal(config.reply_delete_after,45);
 await click('[data-action="tab"][data-id="greet"]');
 input('[data-path="greet.enabled"]',true);
 input('[data-path="greet.channel_id"]','333333333333333333');
 input('[data-path="greet.title"]','Hello <img src=x onerror=alert(1)> {user}');
 assert.match(query('.discord-embed h3').textContent,/<img src=x onerror=alert\(1\)> @wumpus/);
 assert.equal(w.document.querySelectorAll('.discord-embed h3 img').length,0);
 assert.equal(query('#save-bar').hidden,false);
 await click('[data-action="save"]');assert.equal(query('#save-bar').hidden,true);
 assert.equal(config.greet.channel_id,'333333333333333333');
 await click('[data-action="tab"][data-id="staff"]');
 input('#member-search','wumpus');await click('[data-action="find-member"]');await click('[data-action="select-member"]');
 await click('[data-action="grant"][data-id="kick"]');assert.ok(query('[data-action="grant"][data-id="kick"]').classList.contains('selected'));
 await click('[data-action="save-staff"]');assert.deepEqual(staff[0].permissions,['kick']);
 assert.ok(writes.some(([url])=>url.endsWith('/staff/666666666666666666')));
 await click('[data-action="tab"][data-id="tickets"]');await click('[data-action="add-panel"]');
 input('[data-path="ticket.panels.0.title"]','Combined support');
 await click('[data-action="add-option"]');assert.equal(w.document.querySelectorAll('.panel-option').length,2);
 input('[data-path="ticket.panels.0.options.1.label"]','Billing');
 input('[data-path="ticket.panels.0.title"]','Updated combined panel');
 await click('[data-action="save"]');assert.equal(config.ticket.panels[0].options[1].label,'Billing');
 // After saving a newly added panel, its current title must be available for publishing.
 assert.ok([...query('#publish-panel').options].some(o=>o.textContent==='Updated combined panel'));
 await click('[data-action="tab"][data-id="reactions"]');await click('[data-action="add-reaction"]');
 input('[data-path="reaction_roles.0.role_id"]','555555555555555555');
 input('[data-path="reaction_roles.0.channel_id"]','333333333333333333');
 await click('[data-action="save"]');assert.equal(config.reaction_roles[0].role_id,'555555555555555555');
 await click('[data-action="tab"][data-id="embeds"]');await click('[data-action="add-embed-field"]');
 input('[data-path="embeds.fields.0.name"]','Server news');input('[data-path="embeds.fields.0.value"]','New events coming soon.');
 assert.match(query('#custom-preview').textContent,/Server news/);
 await click('[data-action="save"]');assert.equal(config.embeds.fields[0].value,'New events coming soon.');
 await click('[data-action="tab"][data-id="audit"]');
 assert.match(query('td:last-child').textContent,/<script>alert\(1\)<\/script>/);
 assert.equal(w.document.querySelectorAll('#content script').length,0);
 await click('[data-action="tab"][data-id="security"]');
 input('[data-sec="enabled"]',true);input('[data-sec="rules.ban.count"]','4');input('#security-code','owner-code');
 assert.equal(query('#save-bar').hidden,false);
 await click('[data-action="save"]');
 assert.equal(security.policy.enabled,true);assert.equal(security.policy.rules.ban.count,4);
 assert.equal(query('#security-code').value,'');
 await click('[data-action="tab"][data-id="automod"]');
 input('[data-sec="ban_words"]','forbidden phrase');input('[data-sec="repeat_count"]','5');
 input('[data-sec="blocked_domains"]','bad.example\nother.example');input('[data-sec="log_channel_id"]','333333333333333333');
 await click('[data-action="save"]');
 assert.deepEqual(automod.config.blocked_domains,['bad.example','other.example']);
 assert.equal(automod.config.log_channel_id,'333333333333333333');
 assert.deepEqual(automod.config.ban_words,['forbidden phrase']);assert.equal(automod.config.repeat_count,5);
 await click('[data-action="tab"][data-id="greet"]');
 assert.equal(w.document.querySelectorAll('.discord-embed img').length,0);
 assert.deepEqual(errors,[]);
 process.stdout.write('PASS: server selection, welcome preview, escaping, save state, staff green toggles, staff save, ticket panel editing, reaction role IDs, activity log, security confirmation codes, automod controls, plain previews, and no runtime errors.\n');
 dom.window.close();
})().catch(e=>{process.stderr.write(e.stack+'\n');dom.window.close();process.exitCode=1;});
