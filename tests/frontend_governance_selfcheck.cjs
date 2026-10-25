const {readFileSync}=require('node:fs');
const {resolve}=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=readFileSync(resolve(__dirname,'../static/app.js'),'utf8');
const start=source.indexOf('function freezeAccountTarget(');
assert.ok(start>=0,'Freeze actions must resolve a verified institution account, never a wallet address');
const context={Map,Set,Number,String};vm.createContext(context);
vm.runInContext(source.slice(start,source.indexOf('function renderActions(',start)),context);
const nodes=[
  {id:'uid',node_type:'PAKD_USER_ACCOUNT',node_zone:'CRYPTO_EXCHANGE_BRIDGE',entity_label:'Tokocrypto',metadata:{uid:'TKO-9988112'}},
  {id:'receiver',node_type:'PAKD_RECEIVER_ACCOUNT',node_zone:'CRYPTO_EXCHANGE_BRIDGE',entity_label:'Indodax',metadata:{uid:'ID-44'}},
  {id:'bank',node_type:'FINAL_CASHOUT',node_zone:'FIAT_BANK',entity_label:'BRI',account:'1234'},
  ...['UNHOSTED_WALLET','PUBLIC_EXCHANGE_WALLET','DIRECT_EXCHANGE_DEPOSIT','P2P_ESCROW','DEX_POOL','CUSTODIAL_WALLET'].map(node_type=>({id:node_type,node_type,node_zone:'ONCHAIN_WALLET',entity_label:'Tokocrypto',metadata:{uid:'TKO-9988112',recovery_institution:'PAKD'}})),
  {id:'deposit',node_type:'DIRECT_EXCHANGE_DEPOSIT',node_zone:'ONCHAIN_WALLET',metadata:{linked_pakd_account_node_id:'uid'}}
];
const request={request_id:'valid',target_node_id:'uid',institution_category:'PAKD',institution_name:'Tokocrypto',request_status:'NOT_SENT',requested_amount_idr:'300000000',metadata:{target_account_kind:'PAKD_UID_ACCOUNT',target_uid:'TKO-9988112',request_scope:'UNIFIED_PAKD_ACCOUNT',available_amount_idr:'300000000'}};
const graph={nodes,governance_requests:[request],simulation:true,trace_id:'demo'};
const before=JSON.stringify(graph);
assert.equal(context.freezeAccountTarget(graph,request),nodes[0]);
assert.equal(context.freezeAccountTarget(graph,{...request,target_node_id:'receiver',institution_name:'Indodax',metadata:{...request.metadata,target_uid:'ID-44'}}),nodes[1]);
assert.equal(context.freezeAccountTarget(graph,{...request,target_node_id:'bank',institution_category:'PUJK',institution_name:'BRI',metadata:{target_account_kind:'BANK_ACCOUNT'}}),nodes[2]);
for(const node of nodes.slice(3))assert.equal(context.freezeAccountTarget(graph,{...request,target_node_id:node.id}),null,`${node.node_type} is evidence, not an institution account`);
for(const patch of [{institution_name:'Other PAKD'},{institution_category:'STABLECOIN_ISSUER_OR_GLOBAL_EXCHANGE'},{metadata:{...request.metadata,target_uid:'OTHER'}},{metadata:{request_scope:'ACCOUNT_BALANCE'}}])assert.equal(context.freezeAccountTarget(graph,{...request,...patch}),null);
assert.equal(JSON.stringify(graph),before);

function element(tag,className,text){return {tag,className,textContent:text||'',children:[],listeners:{},append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},setAttribute(){},addEventListener(name,handler){this.listeners[name]=handler;}};}
const actionList=element('div'),selected=[];
Object.assign(context,{state:{graph,selected:'UNHOSTED_WALLET',selectedRequestId:'valid',busyRequests:new Set(),packets:new Map()},$:()=>actionList,el:element,selectNode:id=>selected.push(id),decisionStyle:()=>({short:'BELUM DIKIRIM',label:'BELUM DIKIRIM',icon:'○',className:'not-sent'}),idr:String,addDetail(){}});
vm.runInContext(source.slice(source.indexOf('function renderActions('),source.indexOf('function applyGovernanceResult(')),context);
const flatten=node=>[node,...node.children.flatMap(flatten)];
context.renderActions();
assert.ok(!flatten(actionList).some(node=>node.className?.includes('dispatch-button')),'Selecting an external wallet must not display another account’s freeze button');
assert.match(flatten(actionList).map(node=>node.textContent).join(' '),/wallet|on-chain/i);
context.state.selected='deposit';context.renderActions();
const linked=flatten(actionList).find(node=>node.tag==='button');
assert.ok(linked,'A mapped deposit can navigate to its internal PAKD account');
linked.listeners.click();assert.deepEqual(selected,['uid']);
context.state.selected='uid';context.renderActions();
assert.equal(flatten(actionList).filter(node=>node.className?.includes('dispatch-button')).length,1);
assert.match(flatten(actionList).map(node=>node.textContent).join(' '),/TKO-9988112/,'Freeze UI identifies the account by UID');
context.state.selected='P2P_ESCROW';context.renderActions();
assert.equal(flatten(actionList).filter(node=>node.className?.includes('dispatch-button')).length,0);
assert.match(flatten(actionList).map(node=>node.textContent).join(' '),/escrow/i,'Escrow remains supporting order evidence');
assert.ok(flatten(actionList).some(node=>node.tag==='button'),'Escrow can navigate to its verified UID account');
console.log('PASS: wallet/account separation, provider/UID targeting, safe deposit navigation and account-only freeze controls.');
