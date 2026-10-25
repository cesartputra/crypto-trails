const {readFileSync}=require('node:fs');
const {resolve}=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=readFileSync(resolve(__dirname,'../static/app.js'),'utf8');

function mount(){
  const active=[],arrivals=[],particles=[],frames=new Map();let clock=0,frameId=0;
  function element(id){
    const classes=new Set();
    return {id,dataset:{},children:[],hidden:true,disabled:false,textContent:'',attributes:{},
      classList:{add(...values){values.forEach(value=>classes.add(value));if(values.includes('active')&&id.startsWith('edge-'))active.push(id.slice(5));},remove(...values){values.forEach(value=>classes.delete(value));},toggle(value,on){if(on??!classes.has(value))classes.add(value);else classes.delete(value);},contains:value=>classes.has(value)},
      setAttribute(key,value){this.attributes[key]=String(value);},replaceChildren(...children){this.children=children;},append(child){this.children.push(child);child.parent=this;},remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);},
      querySelector(selector){return selector==='.fan-strand'?{getTotalLength:()=>100,getPointAtLength:value=>({x:value,y:0})}:null;},querySelectorAll(){return this.children;}};
  }
  const stages=[['victim','primary'],['primary','mule'],['mule','pakd'],['pakd','hop1'],['hop1','hop2'],['hop2','pakd']].map(([source,target],index)=>({order:index+1,label:`Stage ${index+1}`,sourceIds:[source],targetIds:[target],edges:[{id:`tx${index+1}`,source,target,chronological_step:index+1,amount_idr:'300000000'}]}));
  const graph={nodes:[...new Set(stages.flatMap(step=>[...step.sourceIds,...step.targetIds]))].map(id=>({id})),edges:stages.flatMap(step=>step.edges),governance_requests:[{target_node_id:'pakd',requested_amount_idr:'300000000',request_status:'NOT_SENT'}]};
  const nodes=graph.nodes.map(node=>{const group=element(node.id);group.dataset.nodeId=node.id;return group;});
  const edges=graph.edges.map(edge=>{const group=element(`edge-${edge.id}`);group.dataset={edgeId:edge.id,source:edge.source,target:edge.target};return group;});
  const controls=new Map();const $=selector=>{if(!controls.has(selector))controls.set(selector,element(selector));return controls.get(selector);};
  const context={Map,Set,Number,String,Math,Promise,Date,console,state:{graph,animation:0,flowCursor:-1,viewNodes:graph.nodes,viewEdges:[...graph.edges].reverse().map(edge=>({...edge,members:[edge]})),positions:new Map(),flowProjection:{hops:new Map([['victim',9],['primary',8],['mule',7],['pakd',0],['hop1',1],['hop2',0]])},pins:new Map(),investigationView:'flow',chainFilter:'ALL',timeRange:'ALL',flowMode:'ALL',groupMode:'CHAIN',focusMainPath:false,sortTime:true},$,
    document:{querySelectorAll(selector){if(selector.includes('flow-particle'))return edges.flatMap(group=>group.children);if(selector==='.node,.edge-group')return [...nodes,...edges];return selector.includes('edge-group')?edges:selector.includes('.node')?nodes:[];},createTextNode:text=>text},
    ForensicGraph:{animationSteps:()=>stages,amountSummary:()=> 'Rp300m'},graphMemberEdges:edge=>edge.members||[edge],
    el:(_tag,_class,text)=>text,svgEl(_tag,attributes){const particle=element('particle');particle.attributes=attributes;particles.push({particle,lit:nodes.filter(group=>!group.classList.contains('dimmed')).map(group=>group.id)});return particle;},
    window:{matchMedia:()=>({matches:false})},requestAnimationFrame(fn){const id=++frameId;frames.set(id,fn);queueMicrotask(()=>{if(frames.delete(id)){clock+=350;fn(clock);}});return id;},cancelAnimationFrame:id=>frames.delete(id),setTimeout:fn=>{queueMicrotask(fn);return 1;},clearTimeout(){},
    selectNode(id){arrivals.push(id);},log(){},toast(){},renderGraph(){},fitGraph(){},renderCaseSummary(){},setInvestigationView(){},highlightGraphSelection(){},idr:value=>`Rp${value}`,friendly:value=>String(value).replaceAll('_',' ')};
  vm.createContext(context);
  const start=source.indexOf('function animationPlan(')>=0?source.indexOf('function animationPlan('):source.indexOf('function stopAnimation(');
  vm.runInContext(source.slice(start,source.indexOf('function renderTimeline(',start)),context);
  return {context,active,arrivals,particles,frames,controls,nodes,edges,graph};
}

(async()=>{
  const auto=mount(),before=JSON.stringify(auto.graph);
  await auto.context.animate();
  assert.equal(auto.active[0],'tx1','Autoplay must begin victim → primary scammer even when focal-hop order starts at PAKD');
  assert.deepEqual(auto.active,['tx1','tx2','tx3','tx4','tx5','tx6']);
  assert.equal(auto.arrivals.at(-1),'pakd','Loopback must finish on the same PAKD account');
  assert.ok(auto.particles.length>=6,'Every stage moves a source-to-target particle');
  assert.deepEqual(auto.particles[0].lit,['victim'],'Light the victim before the first target receives funds');
  assert.equal(auto.edges.flatMap(group=>group.children).length,0,'Particles are removed after arrival');
  assert.match(auto.controls.get('#animation-step').textContent,/300000000/,'Final status highlights the existing freeze recommendation amount');
  assert.match(auto.controls.get('#animation-step').textContent,/NOT SENT/,'Animation does not send or approve the recommendation');
  assert.equal(JSON.stringify(auto.graph),before,'Animation never mutates source evidence or governance');
  const manual=mount();
  assert.equal(typeof manual.context.stepFlow,'function','Manual stages use the same planner as autoplay');
  await manual.context.stepFlow(1);await manual.context.stepFlow(1);await manual.context.stepFlow(-1);
  assert.deepEqual(manual.active,['tx1','tx2','tx1']);
  assert.equal(manual.controls.get('#flow-prev-step').disabled,true);
  const cancelled=mount(),pending=cancelled.context.animate();
  cancelled.context.stopAnimation();await pending;
  assert.equal(cancelled.frames.size,0,'Stop cancels the particle RAF');
  assert.equal(cancelled.edges.flatMap(group=>group.children).length,0,'Stop removes particles');
  assert.equal(cancelled.controls.get('#animation-step').hidden,true);
  const inspection=mount();
  Object.assign(inspection.context,{openInspector(){},renderInspector(){},renderActions(){}});
  vm.runInContext(source.slice(source.indexOf('function selectEdge('),source.indexOf('function addDetail(')),inspection.context);
  const inspecting=inspection.context.animate();inspection.context.selectEdge('tx1');await inspecting;
  assert.equal(inspection.context.state.activeAnimationStep,null,'Table inspection cancels playback, just like canvas inspection');
  assert.equal(inspection.context.state.selectedEdge,'tx1');
  assert.equal(inspection.frames.size,0);
  const fitState={positions:new Map([['pakd',{x:0,y:0,radius:38}],['up',{x:0,y:-200,radius:30}],['down',{x:0,y:200,radius:30}]]),focalId:'pakd'};
  const fitContext={state:fitState,Math,updateTransform(){},$:selector=>({
    '#graph-scroll':{clientWidth:1000,clientHeight:720,getBoundingClientRect:()=>({top:100,right:1000})},
    '#inspector-drawer':{hidden:true},'.visualizer-overlay':{clientHeight:100},'.activity-timeline':{clientHeight:100},
    '#animation-step':{hidden:false,getBoundingClientRect:()=>({bottom:320})}
  })[selector]};
  vm.runInNewContext(source.slice(source.indexOf('function fitGraph('),source.indexOf('function zoom(')),fitContext);
  fitContext.fitGraph();
  assert.ok(fitState.y-230*fitState.scale>=230,'Auto-fit keeps every node below the visible animation status banner');
  console.log('PASS: chronological autoplay/manual stages, source-before-arrival particles, loopback recommendation, cancellation and immutable evidence.');
})().catch(error=>{console.error(error);process.exitCode=1;});
