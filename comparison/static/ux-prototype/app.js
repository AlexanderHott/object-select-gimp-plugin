// Throwaway UI study, intentionally independent of inference and GIMP APIs.
const $ = (selector) => document.querySelector(selector);
const variants = {
  A: {title: 'Point at it. Pick it.', eyebrow: 'A / AFFINITY-INSPIRED', description: 'Preview an object under the pointer, then click to select. Switch to parts when you need just the lens or the pot.', integration: 'Main-canvas concept. A native GIMP tool would need core integration. This browser prototype does not register a toolbox tool.', tool: 'hover'},
  B: {title: 'Draw loosely. Refine deliberately.', eyebrow: 'B / PHOTOSHOP-INSPIRED', description: 'Draw around your target, correct the result, then enter a dedicated workspace to inspect and soften the mask.', integration: 'Main-canvas concept. Rectangle, lasso and a separate refinement workspace borrow Photoshop interaction patterns, with simplified controls.', tool: 'box'},
  C: {title: 'One focused selection window.', eyebrow: 'C / GIMP PLUGIN PREVIEW', description: 'Open a preview, mark what to keep, and review the result before applying it to the image in GIMP.', integration: 'Plugin-shaped concept. Interaction happens inside the plugin preview; Apply transfers the result to the simulated GIMP image.', tool: 'include'},
};
const paths = Object.fromEntries(Object.entries(fixture).map(([id, f]) => [id, new Path2D(f.d)]));
const hitContext = document.createElement('canvas').getContext('2d');
let variant = new URLSearchParams(location.search).get('variant') || 'A';
if (!variants[variant]) variant = 'A';
let state, undoStack = [], timer, request = 0, drag = null;
function reset() {
  cancelPending();
  undoStack = [];
  state = {selected: [], hover: [], hoverName: '', tool: variants[variant].tool, mode: 'new', scope: 'object', stage: 'pick', view: 'overlay', feather: 0, output: 'selection', combine: 'new', host: {selection: [], selectionFeather: 0, mask: null}, prompts: [], busy: false, pending: null, message: 'Ready. Choose an object in the image.', dialog: true, actions: 0, applied: false};
  drag = null;
  render();
}
function cancelPending() { clearTimeout(timer); request++; if (state) {state.busy = false; state.pending = null; state.hover = []; state.hoverName = '';} }
function save() { undoStack.push(structuredClone(state)); }
function tell(message) { state.message = message; $('#announcement').textContent = message; }
function changeVariant(key) {
  variant = key;
  const url = new URL(location.href); url.searchParams.set('variant', key); history.replaceState(null, '', url);
  reset();
}
function cycle(offset) { changeVariant(['A','B','C'][(['A','B','C'].indexOf(variant) + offset + 3) % 3]); }
function combine(a, b, mode) {
  if (mode === 'add') return [...new Set([...a, ...b])];
  if (mode === 'subtract') return a.filter(id => !b.includes(id));
  if (mode === 'intersect') return a.filter(id => b.includes(id));
  return [...b];
}
function candidate(id, part = false) {
  if (!id) return [];
  if (part || state.scope === 'part') return [id];
  let ids = Object.keys(fixture).filter(key => fixture[key].group === fixture[id].group);
  if (fixture[id].group === 'camera') {
    if ($('#scenario').value === 'extra') ids.push('strap');
    if ($('#scenario').value === 'missing') ids = ids.filter(key => key !== 'lens');
  }
  return ids;
}
function choose(ids, mode = state.mode, prompt = null) {
  cancelPending();
  if (!ids.length) { tell('No object found there. Try a tighter region or an include point.'); paint(); return; }
  save();
  state.selected = combine(state.selected, ids, mode);
  state.applied = false;
  if (state.stage === 'pick') state.view = 'overlay';
  if (prompt) state.prompts.push(prompt);
  state.actions++;
  tell(state.selected.length ? 'Selection updated. Correct it, refine it, or apply it.' : 'Selection is empty. Pick an object to continue.');
  render();
}
function later(ids, mode, prompt) {
  cancelPending(); const ticket = request;
  state.busy = true; state.pending = 'selection'; tell('Finding the object…'); paint();
  timer = setTimeout(() => { if (ticket === request) choose(ids, mode, prompt); }, Number($('#delay').value));
}
function modeButtons() {return `<div class="segmented">${[['new','New'],['add','+ Add'],['subtract','− Remove'],['intersect','∩']].map(([id,label]) => `<button data-mode="${id}" class="${state.mode === id ? 'active' : ''}" title="${id === 'intersect' ? 'Intersect selections' : label}" aria-pressed="${state.mode === id}">${label}</button>`).join('')}</div>`;}
function toolButtons(tools) {return `<div class="segmented">${tools.map(([id,label]) => `<button data-tool="${id}" class="${state.tool === id ? 'active' : ''}" aria-pressed="${state.tool === id}">${label}</button>`).join('')}</div>`;}
function selectedNames() { return state.selected.length ? [...new Set(state.selected.map(id => fixture[id].name))].join(', ') : 'Nothing selected'; }
function selectionSummary() {return `<div class="selection-items">${state.selected.length ? state.selected.map(id => `<span>${fixture[id].name}</span>`).join('') : '<span>No selection yet</span>'}</div>`;}
function scopeControl() {return `<label for="scope">Target</label><select id="scope"><option value="object" ${state.scope==='object'?'selected':''}>Whole object</option><option value="part" ${state.scope==='part'?'selected':''}>Object part</option></select>`;}
function objectButtons() {return `<details class="named-objects"><summary>Pick by name</summary><div class="object-buttons">${[['body','Camera'],['mug','Cup'],['pot','Plant'],['strap','Strap'],['lens','Lens'],['pot','Pot only']].map(([id,name])=>`<button data-object="${id}">${name}</button>`).join('')}</div></details>`;}
function corrections() {return `<h3>Correct the selection</h3>${toolButtons([['include','+ Include'],['exclude','− Exclude']])}<p class="hint">Click a missing or unwanted part. Right-click also excludes a part.</p><div class="segmented"><button data-action="undo" ${!undoStack.length?'disabled':''}>Undo</button><button data-action="clear" ${!state.selected.length?'disabled':''}>Clear</button></div>`;}
function outputControls() {return `<label for="output">Output</label><select id="output"><option value="selection" ${state.output==='selection'?'selected':''}>GIMP selection</option><option value="mask" ${state.output==='mask'?'selected':''}>Layer mask</option></select>${state.output==='selection'?`<label for="combine">Combine with GIMP selection</label><select id="combine">${[['new','Replace'],['add','Add'],['subtract','Subtract'],['intersect','Intersect']].map(([v,l])=>`<option value="${v}" ${state.combine===v?'selected':''}>${l}</option>`).join('')}</select>`:''}`;}
function viewControls() {return `<label for="view">Preview</label><select id="view">${[['overlay','Selection overlay'],['cutout','On transparency'],['black','On black'],['white','On white'],['mask','Black & white mask']].map(([v,l])=>`<option value="${v}" ${state.view===v?'selected':''}>${l}</option>`).join('')}</select><label for="feather">Soften edge <span id="feather-value">${state.feather} px</span></label><input id="feather" type="range" min="0" max="10" step=".5" value="${state.feather}"><p class="hint">Inspect a different background to check the boundary.</p>`;}
function layers() {return `<aside class="layers"><h3>Layers</h3><div class="layer"><span>◉</span><div class="thumbnail"></div>${state.host.mask?'<div class="mask-thumb">●</div>':''}<span>Still life</span></div><p class="layer-info">Normal · 100% opacity<br>960 × 640 · RGB</p><div class="divider"></div><h3>Image selection</h3><p class="layer-info">${state.host.selection.length ? state.host.selection.map(id=>fixture[id].name).join(', ') : 'None'}</p>${state.host.mask?'<p class="layer-result">Layer mask applied</p>':''}</aside>`;}
function rails() {return '<aside class="tools-rail" aria-hidden="true"><span class="tool-icon">✥</span><span class="tool-icon">▧</span><span class="tool-icon selected">⌖</span><span class="tool-icon">♧</span><span class="tool-icon">⌕</span><span class="tool-icon">╱</span><span class="tool-icon">A</span><span class="rail-colors"></span></aside>';}
function canvas(host = false) {
  return `<div class="canvas-area"><div class="canvas-tab">still-life.xcf ${host ? '' : ' / Still life'} <span style="float:right">${host?'Image':'Selection preview'}</span></div><div class="canvas-mat"><svg class="scene" ${host?'data-host="true"':'id="scene" tabindex="0" role="img" aria-label="Selectable still life. Plant on left, camera in center, cup on right. Use the panel buttons for keyboard selection."'} viewBox="0 0 960 640"><defs>${fixtureDefs}<filter id="${host?'host':'edit'}-soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="${host ? state.host.mask?.feather || 0 : state.feather}"/></filter><mask id="${host?'host':'edit'}-mask" maskUnits="userSpaceOnUse" x="0" y="0" width="960" height="640"><g class="mask-shapes"></g></mask></defs><rect class="preview-bg" width="960" height="640"/><g class="artwork">${sceneArtwork()}</g><g class="selection-overlay" pointer-events="none"></g><g class="hover-overlay" pointer-events="none"></g><g class="gestures" pointer-events="none"></g></svg>${host && variant==='C' && !state.dialog ? '<div class="empty-host"><button class="primary" data-action="open">Open Object Select…</button></div>' : ''}</div><p class="canvas-caption">Illustrated fixture · fixed selectable regions</p><div class="canvas-status"><span class="status-text"></span><span>Fit · 960 × 640</span></div></div>`;
}
function chrome() { return `<div class="window-title"><span class="window-dots"><i></i><i></i><i></i></span>still-life.xcf · GIMP concept</div><div class="mock-menu" aria-hidden="true"><span>GIMP</span><span>File</span><span>Edit</span><span>Select</span><span>View</span><span>Image</span><span>Layer</span><span>Colors</span><span>Filters</span></div>`; }
function VariantA() {
  return `${chrome()}<div class="context-bar"><strong>⌖ Object select</strong>${modeButtons()}<span class="push hint">Hover previews. Click selects.</span></div><div class="workspace">${rails()}<aside class="panel"><h2>Object select</h2>${toolButtons([['hover','⌖ Pick'],['box','▧ Box']])}${scopeControl()}<p class="hint">Choose whole objects or individual parts. The preview shows what a click will select.</p>${corrections()}${objectButtons()}<div class="divider"></div><button data-action="refine" ${!state.selected.length?'disabled':''}>Refine edges…</button></aside>${canvas()}${layers()}</div><div class="context-actions apply-bar">${outputControls()}<button data-action="apply" class="primary" ${!state.selected.length || state.busy?'disabled':''}>Apply to image</button></div>`;
}
function VariantB() {
  return `${chrome()}<div class="context-bar"><strong>Object selection</strong>${toolButtons([['box','▧ Rectangle'],['lasso','♧ Lasso'],['hover','⌖ Finder']])}${modeButtons()}<button data-action="subject" class="push">Select subject</button></div><div class="workspace">${rails()}${canvas()}<aside class="panel"><h2>Selection</h2><p>${state.tool==='lasso'?'Draw a loop around the camera. Release to find the object.':'Draw a loose rectangle around the camera, or use Finder to hover and click.'}</p>${selectionSummary()}${corrections()}${scopeControl()}${objectButtons()}<div class="divider"></div><p class="hint">Next, inspect the cutout in a separate refinement workspace.</p></aside></div><div class="context-actions"><span>${state.selected.length ? 'Selection ready' : 'Draw around an object to begin'}</span><button data-action="refine" ${!state.selected.length?'disabled':''}>Select and refine…</button><button data-action="apply" class="primary" ${!state.selected.length || state.busy?'disabled':''}>Apply ${state.output==='mask'?'layer mask':'selection'}</button></div>`;
}
function VariantC() {
  const review = state.stage === 'review';
  return `${chrome()}<div class="workspace">${rails()}${canvas(true)}${layers()}</div>${state.dialog?`<div class="plugin-shade"><section class="plugin-dialog" role="dialog" aria-label="Object Select plugin preview"><div class="dialog-heading"><div><strong>Object Select</strong><span>Still life · plugin preview</span></div><button data-action="cancel" aria-label="Close plugin preview">×</button></div><div class="dialog-content"><aside class="step-rail"><div class="step ${!state.selected.length?'current':''}"><b>1</b>Pick object</div><div class="step ${state.selected.length&&!review?'current':''}"><b>2</b>Correct</div><div class="step ${review?'current':''}"><b>3</b>Review</div></aside>${canvas()}<aside class="panel">${review?`<h2>Review the output</h2>${viewControls()}${outputControls()}`:`<h2>Mark what to keep</h2>${toolButtons([['include','+ Point'],['box','▧ Box']])}<p class="hint">Click the camera or draw around it. Then mark any missing or extra parts.</p>${selectionSummary()}${corrections()}${objectButtons()}`}</aside></div><div class="dialog-footer"><span class="hint">${review?'Apply updates the image behind this window.':'Your GIMP selection changes only when you apply.'}</span><button data-action="cancel">Cancel</button>${review?'<button data-action="back">Back</button><button data-action="apply" class="primary">Apply to GIMP</button>':`<button data-action="review" class="primary" ${!state.selected.length || state.busy?'disabled':''}>Review selection →</button>`}</div></section></div>`:''}`;
}
function RefineWorkspace() {
  return `${chrome()}<div class="refine-heading"><strong>Refine selection</strong><span class="hint">Inspect the boundary before returning to the image</span></div><div class="workspace">${rails()}${canvas()}<aside class="panel"><h2>Edge preview</h2>${viewControls()}${corrections()}${outputControls()}<div class="divider"></div><div class="stack"><button data-action="accept-refine" class="primary">Keep refinement</button><button data-action="cancel-refine">Cancel refinement</button></div></aside></div>`;
}
let beforeRefine = null;
function render() {
  const active = document.activeElement;
  const activeId = $('#editor').contains(active) ? active.id : null;
  const namedOpen = !!$('.named-objects')?.open;
  const v = variants[variant];
  $('#heading').textContent = v.title; $('#eyebrow').textContent = v.eyebrow;
  $('#description').textContent = v.description; $('#integration').textContent = v.integration;
  const scenario = $('#scenario').value;
  $('#task-text').textContent = scenario==='extra' ? 'Select the camera, remove the strap, then apply a layer mask.' : scenario==='missing' ? 'Select the camera, add the missing lens, then apply a layer mask.' : 'Select the camera and cup together. Inspect the cutout, then apply it.';
  document.querySelectorAll('[data-variant]').forEach(b => b.setAttribute('aria-current', String(b.dataset.variant===variant)));
  $('#editor').className = `editor variant-${variant} ${state.stage==='refine'?'refine':''}`;
  $('#editor').innerHTML = state.stage==='refine' ? RefineWorkspace() : ({A:VariantA,B:VariantB,C:VariantC}[variant])();
  bind(); paint();
  if ($('.named-objects')) $('.named-objects').open = namedOpen;
  if (activeId) document.getElementById(activeId)?.focus({preventScroll: true});
}
function outlinedRegions(ids, attrs) {
  // Remove the internal lens boundary when the entire camera is selected.
  const wholeCamera = ids.includes('body') && ids.includes('lens');
  return ids.filter(id => !wholeCamera || id !== 'lens').map(id => {
    if (wholeCamera && id === 'body') return `<path d="${fixture.body.d.split(' M614')[0]}" ${attrs}/>`;
    return fixturePath(id, attrs);
  }).join('');
}
function paint() {
  document.querySelectorAll('.scene').forEach(svg => {
    const host = !!svg.dataset.host;
    const ids = host ? state.host.mask?.ids || state.host.selection : state.selected;
    const view = host ? state.host.mask ? 'cutout' : 'overlay' : state.view;
    const prefix = host?'host':'edit';
    svg.querySelector('.mask-shapes').innerHTML = ids.map(id=>fixturePath(id, 'fill="white"')).join('');
    svg.querySelector('.mask-shapes').setAttribute('filter', `url(#${prefix}-soft)`);
    const art = svg.querySelector('.artwork');
    if (view==='overlay') art.removeAttribute('mask'); else art.setAttribute('mask', `url(#${prefix}-mask)`);
    art.style.display = view==='mask'?'none':'';
    const bg = svg.querySelector('.preview-bg');
    bg.setAttribute('fill', view==='black'||view==='mask'?'#111':view==='white'?'#fff':'url(#checker)');
    svg.querySelector('.selection-overlay').innerHTML = view==='mask' ? `<rect width="960" height="640" fill="white" mask="url(#${prefix}-mask)"/>` : view==='overlay' ? outlinedRegions(ids,'class="under-ants"')+outlinedRegions(ids,'class="ants"') : '';
    svg.querySelector('.hover-overlay').innerHTML = host?'':outlinedRegions(state.hover,`class="candidate ${state.mode}"`);
    if (!host) svg.querySelector('.gestures').innerHTML = state.prompts.map(p=>p.x===undefined?'':`<circle class="prompt" cx="${p.x}" cy="${p.y}" r="7" fill="${p.mode==='subtract'?'#d75d60':'#5cbe86'}"/><path d="M${p.x-3} ${p.y}h6${p.mode!=='subtract'?`M${p.x} ${p.y-3}v6`:''}" stroke="white"/>`).join('') + (drag ? drag.tool==='lasso'?`<path class="box" d="M${drag.points.map(p=>`${p.x},${p.y}`).join('L')}Z"/>`:`<rect class="box" x="${Math.min(drag.start.x,drag.end.x)}" y="${Math.min(drag.start.y,drag.end.y)}" width="${Math.abs(drag.end.x-drag.start.x)}" height="${Math.abs(drag.end.y-drag.start.y)}"/>` : '');
  });
  document.querySelectorAll('.status-text').forEach(el=>{el.textContent=state.busy?'Finding the object…':state.hoverName?`Preview: ${state.hoverName}. Click to select.`:state.message;});
  document.querySelectorAll('[data-action="apply"], [data-action="review"], [data-action="refine"]').forEach(b => { b.disabled = !state.selected.length || state.busy && state.pending === 'selection'; });
  const target = $('#scenario').value==='clean'?['body','lens','mug']:['body','lens'];
  const ids = state.applied ? state.output==='mask'?state.host.mask?.ids || []:state.host.selection : state.selected;
  const correct = target.every(id=>ids.includes(id)) && ids.length===target.length;
  $('#task-progress').textContent = state.applied && correct && ($('#scenario').value==='clean' || state.output==='mask') ? '✓ Task complete' : state.applied ? 'Applied · compare with the task' : `${state.actions} selection ${state.actions===1?'action':'actions'}`;
  $('#state').textContent = JSON.stringify({variant, fixture: 'fixed vector regions', scenario: $('#scenario').value, delayMs: Number($('#delay').value), ...state, undoDepth: undoStack.length, gesture: drag}, null, 2);
}
function bind() {
  document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{cancelPending();state.mode=b.dataset.mode;render();});
  document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>{cancelPending();state.tool=b.dataset.tool;render();});
  document.querySelectorAll('[data-object]').forEach(b=>b.onclick=()=>later(candidate(b.dataset.object, state.tool==='exclude' || b.dataset.object==='lens' || b.textContent==='Pot only' || state.tool==='include' && !!state.selected.length), state.tool==='exclude'?'subtract':state.tool==='include'&&state.selected.length?'add':state.mode));
  document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>action(b.dataset.action));
  ['scope','output','combine','view'].forEach(key=>{if ($('#'+key)) $('#'+key).onchange=e=>{cancelPending();state[key]=e.target.value;render();};});
  if ($('#feather')) $('#feather').oninput=e=>{state.feather=Number(e.target.value);$('#feather-value').textContent=`${state.feather} px`;$('#scene feGaussianBlur').setAttribute('stdDeviation',state.feather);paint();};
  const svg = $('#scene'); if (!svg) return;
  svg.oncontextmenu=e=>e.preventDefault();
  svg.onpointerdown=pointerDown; svg.onpointermove=pointerMove; svg.onpointerup=pointerUp;
  svg.onpointerleave=()=>{if (!drag && !state.busy) {state.hover=[];state.hoverName='';paint();} else if (!drag && state.pending==='hover') {cancelPending();paint();}};
  svg.onpointercancel=()=>{drag=null;cancelPending();paint();};
}
function point(event) {const p=new DOMPoint(event.clientX,event.clientY).matrixTransform($('#scene').getScreenCTM().inverse());return {x:Math.round(p.x),y:Math.round(p.y)};}
function hit(p) {return Object.keys(fixture).reverse().find(id=>hitContext.isPointInPath(paths[id],p.x,p.y,'evenodd'));}
function pointerDown(e) {
  if (e.button!==0 && e.button!==2) return;
  const p=point(e), id=hit(p);
  if (e.button===2 || state.tool==='exclude') {later(id?[id]:[], 'subtract', {...p,mode:'subtract'});return;}
  if (['box','lasso'].includes(state.tool)) {cancelPending();drag={start:p,end:p,points:[p],tool:state.tool,mode:e.shiftKey?'add':e.altKey?'subtract':state.mode};e.currentTarget.setPointerCapture(e.pointerId);paint();return;}
  if (state.tool==='include') {later(candidate(id,!!state.selected.length), state.selected.length?'add':'new', {...p,mode:'add'});return;}
  if (state.tool==='hover') {
    const ids=candidate(id);
    // A click waits for this target's own result, never commits the previous hover.
    later(ids,e.shiftKey?'add':e.altKey?'subtract':state.mode);
  }
}
let lastHover = '';
function pointerMove(e) {
  const p=point(e);
  if (drag) {drag.end=p;drag.points.push(p);paint();return;}
  if (state.tool!=='hover' || state.pending==='selection') return;
  const id=hit(p), ids=candidate(id), key=ids.join(',');
  if (key===lastHover && (state.hover.length || state.busy)) return;
  lastHover=key;cancelPending();
  if (!ids.length) {paint();return;}
  const ticket=request;state.busy=true;state.pending='hover';paint();
  timer=setTimeout(()=>{if(ticket!==request)return;state.busy=false;state.pending=null;state.hover=ids;state.hoverName=state.scope==='part'?fixture[id].name:groupNames[fixture[id].group];paint();},Number($('#delay').value));
}
function pointerUp(e) {
  if (!drag) return;
  const d=drag; d.end=point(e);drag=null;
  const region=new Path2D();
  if (d.tool==='lasso') {region.moveTo(d.points[0].x,d.points[0].y);d.points.forEach(p=>region.lineTo(p.x,p.y));region.closePath();}
  else region.rect(Math.min(d.start.x,d.end.x),Math.min(d.start.y,d.end.y),Math.abs(d.end.x-d.start.x),Math.abs(d.end.y-d.start.y));
  // Rank fixed objects by the fraction covered; a loose region snaps to objects.
  const groups={};
  for (const id of Object.keys(fixture)) {
    let total=0, inside=0;
    for(let x=120;x<885;x+=10)for(let y=90;y<550;y+=10)if(hitContext.isPointInPath(paths[id],x,y,'evenodd')){total++;if(hitContext.isPointInPath(region,x,y))inside++;}
    if(total && inside/total>.45) groups[state.scope==='part'?id:fixture[id].group]=id;
  }
  const ids=[...new Set(Object.values(groups).flatMap(id=>candidate(id)))];
  later(ids,d.mode,{type:d.tool,from:d.start,to:d.end,mode:d.mode});
}
function action(name) {
  if (name==='undo') {cancelPending();if(undoStack.length){state=undoStack.pop();state.hover=[];state.hoverName='';state.busy=false;tell('Undid the last change.');}render();return;}
  if (name==='clear') {cancelPending();save();state.selected=[];state.prompts=[];state.applied=false;tell('Selection cleared.');render();return;}
  if (name==='subject') {later(candidate('body'),'new');return;}
  if (name==='refine') {cancelPending();beforeRefine={state:structuredClone(state), undoDepth:undoStack.length};state.stage='refine';state.view='cutout';state.tool='include';render();return;}
  if (name==='cancel-refine') {cancelPending();state=beforeRefine.state;undoStack.length=beforeRefine.undoDepth;beforeRefine=null;render();return;}
  if (name==='accept-refine') {cancelPending();undoStack.length=beforeRefine.undoDepth;undoStack.push(beforeRefine.state);state.stage='pick';state.view='overlay';state.tool=variants[variant].tool;beforeRefine=null;tell('Refinement kept. Apply when ready.');render();return;}
  if (name==='review') {cancelPending();state.stage='review';state.view='cutout';state.tool='include';render();return;}
  if (name==='back') {state.stage='pick';state.view='overlay';render();return;}
  if (name==='cancel') {cancelPending();state.selected=[];state.prompts=[];state.dialog=false;state.stage='pick';state.view='overlay';undoStack=[];tell('Plugin closed. The image was not changed.');render();return;}
  if (name==='open') {state.dialog=true;state.stage='pick';state.selected=[];state.prompts=[];state.feather=0;state.view='overlay';state.tool='include';state.applied=false;tell('Choose an object in the plugin preview.');render();return;}
  if (name==='apply') {
    if (!state.selected.length || state.busy) return;
    cancelPending();save();
    if (state.output==='mask') state.host.mask={ids:[...state.selected],feather:state.feather};
    else {state.host.selection=combine(state.host.selection,state.selected,state.combine);state.host.selectionFeather=state.feather;}
    state.applied=true;
    state.view=state.output==='mask'?'cutout':'overlay';
    if (variant==='C') state.dialog=false;
    tell(`Applied ${state.output==='mask'?'a layer mask':'the selection'} to the simulated image. No GIMP document was changed.`);
    render();
  }
}
$('#reset').onclick=reset;
$('#scenario').onchange=reset;
$('#delay').onchange=()=>{cancelPending();paint();};
$('#previous').onclick=()=>cycle(-1);$('#next').onclick=()=>cycle(1);
document.querySelectorAll('[data-variant]').forEach(b=>b.onclick=()=>changeVariant(b.dataset.variant));
window.onpopstate=()=>{variant=new URLSearchParams(location.search).get('variant')||'A';if(!variants[variant])variant='A';reset();};
document.addEventListener('keydown',e=>{
  if(e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
  if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();cycle(e.key==='ArrowLeft'?-1:1);}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();action('undo');}
  if(e.key==='Escape'){if(drag || state.busy){drag=null;cancelPending();tell('Pending selection cancelled.');paint();}else if(state.stage==='refine')action('cancel-refine');else if(variant==='C'&&state.dialog)action('cancel');else{state.hover=[];state.hoverName='';paint();}}
});
reset();
