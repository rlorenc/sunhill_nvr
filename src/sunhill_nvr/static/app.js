const $ = (id) => document.getElementById(id);
let me = null;
let cameras = [];
let cameraListState = "loading";
let layouts = [];
let activeCamera = null;
let records = [];
let eventMarkers = [];
let recordPosition = 0;
let playbackGeneration = 0;
let archiveLoadGeneration = 0;
let loadedArchiveDate = null;
let archiveRecordId = null;
let archiveRequestedDate = null;
let archiveScrubbing = false;
let calendarGeneration = 0;
let archiveAutoRecovery=null;
let archiveErrorGeneration=-1;
const archiveCompatCameras=new Set();
let archivePlaybackIntent=null;
let exportListGeneration = 0;
let exportListPending = false;
let exportListRefreshAgain = false;
let timelineRequestGeneration = 0;
let timelineSelection = null;
let timelineFormatter = null;
let timelineFormatterZone = null;
let cameraRefreshGeneration = 0;
let statusRefreshPending = false;
let adminRefreshGeneration = 0;
let storageDirty=false, storageRequest=null, storagePolling=false, storageCurrent=null, storageLocalMessage=null;
function renderStorage(storage){
  const form=$('archive-form');
  storageCurrent=storage;
  $('archive-path').textContent='Nagrania: '+storage.archive+' | Eksporty: '+storage.exports+' | Bufor: '+(storage.staging||'nieznany');
  const state=storage.change_status||{};
  if(storageRequest){
    if(state.request_id===storageRequest && ['done','failed','recovery_required'].includes(state.state) && !storage.change_pending){
      storageRequest=null;storageLocalMessage=state.message;
      storageDirty=state.state!=='done';
      $('storage-change-status').textContent=state.message;
    }else if(!storage.change_pending && state.request_id!==storageRequest){
      // An old recovery coordinator cannot handle a correlated request.
      storageRequest=null;storageDirty=true;
      storageLocalMessage='Brak potwierdzenia bieżącej operacji. Sprawdź wersję recovery i dziennik usługi.';$('storage-change-status').textContent=storageLocalMessage;
    }else $('storage-change-status').textContent=state.request_id===storageRequest?state.message:'Zmiana katalogów oczekuje na przetworzenie.';
  }else $('storage-change-status').textContent=storage.change_pending?'Zmiana katalogów oczekuje na przetworzenie.':storageLocalMessage||(state.message?'Ostatnia operacja: '+state.message:'');
  for(const name of ['archive','exports','staging'])form.elements[name].readOnly=Boolean(storageRequest||storage.change_pending);
  if(!storageDirty&&!storageRequest){form.elements.archive.value=storage.archive;form.elements.exports.value=storage.exports;form.elements.staging.value=storage.staging||'';}
}
async function pollStorage(){
  if(storagePolling||!me||me.role!=='admin'||(!storageRequest&&$('admin-view').hidden))return;
  storagePolling=true;
  try{const value=await api('/api/settings/storage');if(me&&me.role==='admin')renderStorage(value);}catch(error){/* Maintenance may temporarily return 503. Preserve draft and retry. */}finally{storagePolling=false;}
}
let exportViewGeneration = 0;
let exportTimer = null;
let exportSubmitting = false;
let protectionSubmitting = false;
let livePlayerLoading = false;
let livePlayerRetry = null;
let livePlayerAttempts = 0;
const livePreferences = new Map();
// The personal workspace is separate from server-side named camera layouts.
// CSS order/spans update in place: moving a custom element in the DOM would
// disconnect its live WebSocket and reset audio and MSE state.
let liveLayoutEditing=false,liveLayoutOrder=[],liveLayoutGesture=null;
const liveLayoutSizes=new Map();
let workspaceOwner=null,workspaceReady=false,workspacePending=null,workspaceSavePromise=null;
function queueLiveWorkspace(){
  if(!workspaceReady||typeof me==='undefined'||me?.id!==workspaceOwner)return;
  workspacePending={layout_id:Number($('layout-select').value)||null,order:liveLayoutOrder.map(Number),sizes:Object.fromEntries(liveLayoutSizes)};
  void flushLiveWorkspace();
}
async function flushLiveWorkspace(){
  if(workspaceSavePromise)return workspaceSavePromise;
  if(!workspacePending||!workspaceReady)return;
  const owner=workspaceOwner;
  workspaceSavePromise=(async()=>{
    while(workspacePending&&me?.id===owner){
      const payload=workspacePending;workspacePending=null;
      try{await api('/api/live-workspace','POST',payload);}
      catch(error){if(me?.id===owner){workspacePending=workspacePending||payload;notify('Nie zapisano układu kamer: '+error.message,'error');}break;}
    }
  })();
  try{await workspaceSavePromise;}finally{workspaceSavePromise=null;}
}
async function restoreLiveWorkspace(){
  if(!me||workspaceReady&&workspaceOwner===me.id)return;
  const owner=me.id;workspaceOwner=owner;workspaceReady=false;workspacePending=null;liveLayoutOrder=[];liveLayoutSizes.clear();
  const result=await api('/api/live-workspace');if(me?.id!==owner)return;
  const state=result.workspace;
  if(state){liveLayoutOrder=state.order.map(String);for(const [id,size] of Object.entries(state.sizes))liveLayoutSizes.set(id,size);$('layout-select').value=state.layout_id==null?'':String(state.layout_id);}
  reconcileLiveLayout();workspaceReady=true;
}

function liveGridMetrics(width,count){
  width=Math.max(1,width||960);
  const columns=Math.max(1,Math.min(count||1,4,Math.floor((width+10)/(width<1000?330:390))));
  return {defaultSpan:12/columns,minSpan:width<600?12:Math.min(12,Math.ceil(12*280/width)),step:(width+10)/12};
}
function reconcileLiveLayout(){
  const ids=cameras.map(camera=>String(camera.id)),valid=new Set(ids);
  liveLayoutOrder=liveLayoutOrder.filter(id=>valid.has(id));
  for(const id of ids)if(!liveLayoutOrder.includes(id))liveLayoutOrder.push(id);
  for(const id of liveLayoutSizes.keys())if(!valid.has(id))liveLayoutSizes.delete(id);
}
function liveLayoutCards(){return Array.from($('cameras').querySelectorAll('.camera'));}
function liveLayoutMetrics(){const area=$('cameras');return liveGridMetrics(area.clientWidth,Number(area.dataset.count)||1);}
function liveOrderedCards(){return liveLayoutCards().sort((a,b)=>liveLayoutOrder.indexOf(a.dataset.cameraId)-liveLayoutOrder.indexOf(b.dataset.cameraId));}
function liveSpan(value,metrics){return Math.max(metrics.minSpan,Math.min(12,Math.round(value)));}
function liveHeight(value){return Math.max(140,Math.min(900,Math.round(value)));}
function applyLiveLayout(){
  const area=$('cameras'),cards=liveLayoutCards(),metrics=liveLayoutMetrics();
  const reordered=liveLayoutOrder.join(',')!==cameras.map(c=>String(c.id)).join(',');
  area.classList.toggle('live-custom-grid',liveLayoutEditing||reordered||liveLayoutSizes.size>0);
  area.classList.toggle('live-layout-editing',liveLayoutEditing);
  $('edit-live-layout').textContent=liveLayoutEditing?'Zakończ edycję':'Edytuj widok';
  $('edit-live-layout').setAttribute('aria-pressed',String(liveLayoutEditing));
  $('live-layout-help').hidden=!liveLayoutEditing;
  $('reset-live-layout').hidden=!(liveLayoutEditing||reordered||liveLayoutSizes.size);
  for(const card of cards){
    const id=card.dataset.cameraId,size=liveLayoutSizes.get(id)||{};
    card.style.order=String(liveLayoutOrder.indexOf(id));
    card.style.setProperty('--live-span',String(liveSpan(size.span??metrics.defaultSpan,metrics)));
    if(size.height!=null)card.style.setProperty('--live-height',size.height+'px');
    else card.style.removeProperty('--live-height');
  }
}
function moveLiveCamera(id,target){
  const visible=liveOrderedCards().map(card=>card.dataset.cameraId),from=visible.indexOf(id),to=visible.indexOf(target);
  if(from<0||to<0||from===to)return false;
  visible.splice(from,1);visible.splice(to,0,id);
  const chosen=new Set(visible);let next=0;
  liveLayoutOrder=liveLayoutOrder.map(key=>chosen.has(key)?visible[next++]:key);
  applyLiveLayout();queueLiveWorkspace();return true;
}
function stopLiveLayoutGesture(){if(liveLayoutGesture)liveLayoutGesture.cancel();}
function setLiveLayoutEditing(editing){stopLiveLayoutGesture();liveLayoutEditing=editing;applyLiveLayout();}
function resetLiveLayout(){stopLiveLayoutGesture();liveLayoutOrder=cameras.map(c=>String(c.id));liveLayoutSizes.clear();applyLiveLayout();queueLiveWorkspace();}
function installLiveLayoutHandles(card,camera){
  const id=String(camera.id),controls=element('div',null,'live-layout-handles');
  const move=element('button','Przesuń','live-move-handle'),resize=element('button','Zmień rozmiar','live-resize-handle');
  for(const handle of [move,resize])handle.type='button';
  move.setAttribute('aria-label','Przesuń kamerę '+camera.name+' (strzałki: poprzednie lub następne miejsce)');
  resize.setAttribute('aria-label','Zmień rozmiar kamery '+camera.name+' (strzałki: szerokość i wysokość)');
  const expanded=()=>card.classList.contains('expanded');
  const mediaHeight=()=>{const media=card.querySelector('video-stream, p.camera-actions');return media?media.getBoundingClientRect().height:card.getBoundingClientRect().width*9/16;};
  function start(event,isResize,handle){
    if(!liveLayoutEditing||event.isPrimary===false||event.button!==0||liveLayoutGesture)return;
    event.preventDefault();
    const pointer=event.pointerId,metrics=liveLayoutMetrics(),initial=liveLayoutSizes.get(id),rect=card.getBoundingClientRect();
    const baseSpan=liveSpan(initial?.span??Math.round((rect.width+10)/metrics.step),metrics),baseHeight=mediaHeight();
    const startX=event.clientX,startY=event.clientY;let target=null,moved=false;
    const clearTarget=()=>{if(target)target.classList.remove('live-drop-target');target=null;};
    function update(e){
      if(e.pointerId!==pointer)return;
      const dx=e.clientX-startX,dy=e.clientY-startY;
      if(Math.abs(dx)+Math.abs(dy)<4&&!moved)return;
      moved=true;
      if(isResize){
        const size={...initial};
        if(!expanded()&&Math.abs(dx)>=metrics.step/2)size.span=liveSpan(baseSpan+dx/metrics.step,metrics);
        else if(!expanded()&&size.span!=null)size.span=initial.span;
        if(Math.abs(dy)>=4)size.height=liveHeight(baseHeight+dy);
        if(size.span==null&&size.height==null)liveLayoutSizes.delete(id);else liveLayoutSizes.set(id,size);
        applyLiveLayout();
      }else{
        const hovered=document.elementFromPoint(e.clientX,e.clientY)?.closest('.camera');
        const next=hovered!==card&&hovered&&hovered.parentElement===$('cameras')?hovered:null;
        if(next!==target){clearTarget();target=next;if(target)target.classList.add('live-drop-target');}
      }
    }
    function finish(commit){
      if(liveLayoutGesture?.pointer!==pointer)return;
      liveLayoutGesture=null;
      if(isResize&&!commit){if(initial)liveLayoutSizes.set(id,initial);else liveLayoutSizes.delete(id);}
      const destination=target?.dataset.cameraId;clearTarget();card.classList.remove('live-tile-moving','live-tile-resizing');
      for(const [name,fn] of listeners)handle.removeEventListener(name,fn);
      if(handle.hasPointerCapture(pointer))handle.releasePointerCapture(pointer);
      if(commit&&!isResize&&destination)moveLiveCamera(id,destination);else {applyLiveLayout();if(commit&&isResize&&moved)queueLiveWorkspace();}
    }
    const end=e=>{if(e.pointerId===pointer){update(e);finish(true);}};
    const cancel=e=>{if(e.pointerId===pointer)finish(false);};
    const listeners=[['pointermove',update],['pointerup',end],['pointercancel',cancel],['lostpointercapture',cancel]];
    try{handle.setPointerCapture(pointer);}catch{return;}
    liveLayoutGesture={pointer,cancel:()=>finish(false)};
    card.classList.add(isResize?'live-tile-resizing':'live-tile-moving');
    for(const [name,fn] of listeners)handle.addEventListener(name,fn);
  }
  move.addEventListener('pointerdown',event=>start(event,false,move));
  resize.addEventListener('pointerdown',event=>start(event,true,resize));
  move.addEventListener('keydown',event=>{
    if(!liveLayoutEditing||!['ArrowLeft','ArrowUp','ArrowRight','ArrowDown'].includes(event.key))return;
    event.preventDefault();const cards=liveOrderedCards(),at=cards.indexOf(card),next=cards[at+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)];
    if(next)moveLiveCamera(id,next.dataset.cameraId);
  });
  resize.addEventListener('keydown',event=>{
    if(!liveLayoutEditing||!['ArrowLeft','ArrowUp','ArrowRight','ArrowDown'].includes(event.key))return;
    event.preventDefault();const metrics=liveLayoutMetrics(),size={...liveLayoutSizes.get(id)};
    if(['ArrowLeft','ArrowRight'].includes(event.key)){
      if(expanded())return;
      const span=size.span??Math.round((card.getBoundingClientRect().width+10)/metrics.step);
      size.span=liveSpan(liveSpan(span,metrics)+(event.key==='ArrowRight'?1:-1),metrics);
    }else size.height=liveHeight((size.height??mediaHeight())+(event.key==='ArrowDown'?40:-40));
    liveLayoutSizes.set(id,size);applyLiveLayout();queueLiveWorkspace();
  });
  controls.append(move,resize);card.append(controls);
}
$('edit-live-layout').addEventListener('click',()=>setLiveLayoutEditing(!liveLayoutEditing));
$('reset-live-layout').addEventListener('click',resetLiveLayout);
if(typeof ResizeObserver!=='undefined'){
  let lastWidth=0;
  new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(width&&width!==lastWidth){lastWidth=width;applyLiveLayout();}}).observe($('cameras'));
}

// Zoom only the decoded picture, not the surrounding tile or controls.
// Translation is normalized to the viewport so resizing preserves the crop.
function digitalZoomGeometry(state,scale,ax,ay){
  const next=Math.max(1,Math.min(8,scale)),ratio=next/state.scale;
  return {scale:next,x:Math.max(1-next,Math.min(0,ax-(ax-state.x)*ratio)),y:Math.max(1-next,Math.min(0,ay-(ay-state.y)*ratio))};
}
function installDigitalZoom(viewport,targets,enabled=()=>true){
  let state={scale:1,x:0,y:0},gesture=null,suppressClickUntil=0;
  const apply=()=>{
    for(const target of targets().filter(Boolean)){
      if(state.scale===1){target.style.removeProperty('transform');target.style.removeProperty('transform-origin');}
      else{target.style.transformOrigin='0 0';target.style.transform=`translate(${state.x*100}%,${state.y*100}%) scale(${state.scale})`;}
    }
    viewport.classList.toggle('digital-zoom-active',state.scale>1);
  };
  const reset=()=>{state={scale:1,x:0,y:0};apply();};
  viewport.addEventListener('wheel',event=>{
    if(!enabled()||event.ctrlKey||event.deltaY===0||!targets()[0])return;
    event.preventDefault();const rect=viewport.getBoundingClientRect();if(!rect.width||!rect.height)return;
    const ax=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),ay=Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height));
    state=digitalZoomGeometry(state,state.scale*(event.deltaY<0?1.25:0.8),ax,ay);apply();
  },{passive:false});
  viewport.addEventListener('pointerdown',event=>{
    if(!enabled()||state.scale===1||event.button!==0||event.isPrimary===false||gesture||event.target.closest?.('button,input,select'))return;
    const rect=viewport.getBoundingClientRect();if(!rect.width||!rect.height)return;
    gesture={pointer:event.pointerId,startX:event.clientX,startY:event.clientY,x:state.x,y:state.y,width:rect.width,height:rect.height,moved:false};
  });
  viewport.addEventListener('pointermove',event=>{
    if(!gesture||event.pointerId!==gesture.pointer)return;
    const dx=event.clientX-gesture.startX,dy=event.clientY-gesture.startY;
    if(!gesture.moved&&Math.abs(dx)+Math.abs(dy)<4)return;
    if(!gesture.moved){try{viewport.setPointerCapture(event.pointerId);}catch{}gesture.moved=true;}
    event.preventDefault();state={scale:state.scale,x:Math.max(1-state.scale,Math.min(0,gesture.x+dx/gesture.width)),y:Math.max(1-state.scale,Math.min(0,gesture.y+dy/gesture.height))};apply();
  });
  function finish(event){if(!gesture||event.pointerId!==gesture.pointer)return;const moved=gesture.moved;gesture=null;if(moved&&event.type==='pointerup')suppressClickUntil=Date.now()+500;if(viewport.hasPointerCapture?.(event.pointerId))viewport.releasePointerCapture(event.pointerId);}
  for(const name of ['pointerup','pointercancel','lostpointercapture'])viewport.addEventListener(name,finish);
  viewport.addEventListener('pointerleave',()=>{if(gesture&&!gesture.moved)gesture=null;});
  viewport.addEventListener('click',event=>{if(suppressClickUntil&&Date.now()<=suppressClickUntil){suppressClickUntil=0;event.preventDefault();event.stopImmediatePropagation();}},true);
  viewport.addEventListener('keydown',event=>{if(event.key==='Escape'&&state.scale>1){event.preventDefault();reset();}});
  return {apply,reset};
}

function liveQualityCookie(camera){return 'sunhill_quality_'+encodeURIComponent(String(me?.id??me?.username??me?.role??'user')+'_'+camera.id+'_'+camera.slug);}
function savedLiveQuality(camera){
  try{const value=(document.cookie||'').split(';').map(x=>x.trim()).find(x=>x.startsWith(liveQualityCookie(camera)+'='))?.split('=')[1];return ['sub','main'].includes(value)?value:null;}catch{return null;}
}
function saveLiveQuality(camera,value){
  if(!['sub','main'].includes(value))return;
  try{document.cookie=liveQualityCookie(camera)+'='+value+'; Path=/; Max-Age=31536000; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'');}catch{/* Browser may disable cookies. In-memory preference remains. */}
}
function selectedLiveAlias(camera, quality, enlarged){
  if(quality==='compatible')return '~'+camera.slug;
  if(quality==='sub'&&camera.sub_configured)return camera.slug+'_sub';
  if(quality==='auto'&&!enlarged&&camera.sub_configured)return camera.slug+'_sub';
  return camera.slug;
}
const liveControlObservers=new WeakMap();
function suppressNativeLiveControls(owner){
  const native=owner.video;
  if(native)native.controls=false;
}
function watchNativeLiveControls(owner){
  suppressNativeLiveControls(owner);
  if(typeof MutationObserver==='undefined')return;
  let observed=null;
  const observer=new MutationObserver(()=>sync());
  function sync(){
    const native=owner.video;
    if(native!==observed){
      observer.disconnect();observer.observe(owner,{childList:true,subtree:true,attributes:true,attributeFilter:['controls']});
      if(owner.shadowRoot)observer.observe(owner.shadowRoot,{childList:true,subtree:true,attributes:true,attributeFilter:['controls']});
      if(native)observer.observe(native,{attributes:true,attributeFilter:['controls']});
      observed=native;
    }
    // Avoid an attribute mutation loop when upstream enables controls.
    if(native&&native.controls)native.controls=false;
  }
  observer.observe(owner,{childList:true,subtree:true,attributes:true,attributeFilter:['controls']});
  sync();liveControlObservers.set(owner,observer);
}
function closeLivePlayer(player){
  liveControlObservers.get(player)?.disconnect();liveControlObservers.delete(player);
  if(typeof player.ondisconnect==='function')player.ondisconnect();
}

function livePlayerReady(){return typeof customElements!=='undefined'&&Boolean(customElements.get('video-stream'));}
function ensureLivePlayer(){
  if(!me||typeof customElements==='undefined'||livePlayerReady()||livePlayerLoading||livePlayerRetry!==null)return;
  livePlayerLoading=true;
  const script=document.createElement('script');script.type='module';
  const attempt=++livePlayerAttempts;
  // Keep the original gateway route as a compatibility fallback after two failures.
  const nativeFallback=attempt>=3;
  script.src=(nativeFallback?'/live/player/':'/assets/live-player/')+attempt+'/video-stream.js';
  const retry=()=>{
    livePlayerLoading=false;script.remove();
    if(!me||livePlayerReady())return;
    const delay=Math.min(60000,1000*2**Math.min(livePlayerAttempts-1,6));
    livePlayerRetry=setTimeout(()=>{livePlayerRetry=null;ensureLivePlayer();},delay);
  };
  script.onerror=retry;
  script.onload=()=>{
    if(!livePlayerReady()){retry();return;}
    if(nativeFallback)globalThis.sunhillMsePolicy='native';
    livePlayerLoading=false;livePlayerAttempts=0;script.remove();
    // Create players only after their custom element has been registered.
    if(me&&!$('camera-view').hidden)renderCameras();
  };
  document.head.append(script);
}

let noticeTimer=null;
function notify(message,kind='info') {
  clearTimeout(noticeTimer);$('notice').textContent=message||'';$('notice').dataset.kind=kind;
  if(message)noticeTimer=setTimeout(()=>{$('notice').textContent='';noticeTimer=null;},10000);
}
function cookie(name) {
  const item = document.cookie.split('; ').find((part) => part.startsWith(name + '='));
  return item ? decodeURIComponent(item.slice(name.length + 1)) : '';
}
async function api(url, method = 'GET', data = null) {
  const options = {method, credentials:'same-origin', headers:{}};
  if (method !== 'GET') options.headers['X-CSRF-Token'] = cookie('sunhill_csrf');
  if (data !== null) {options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
  const response = await fetch(url, options);
  let value;try{value=await response.json();}catch{throw new Error(response.status===503?'Serwer jest chwilowo niedostępny.':'Nieprawidłowa odpowiedź serwera.');}
  if (!response.ok) {const error=new Error(value.error || 'Błąd żądania');error.status=response.status;throw error;}
  return value;
}
function element(tag, text, className) {
  const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node;
}
function formData(form) { return Object.fromEntries(new FormData(form)); }
async function archivePages(url,key,stillCurrent=()=>true){
  const items=[];let cursor=null;const seen=new Set();
  do {
    if(!stillCurrent())return {[key]:[]};
    const page=await api(url+(cursor?'&cursor='+encodeURIComponent(cursor):''));
    if(!stillCurrent())return {[key]:[]};
    if(!Array.isArray(page[key]))throw new Error('Nieprawidłowa odpowiedź archiwum.');
    items.push(...page[key]);
    cursor=page.next_cursor||null;
    if(cursor&&(typeof cursor!=='string'||seen.has(cursor)||seen.size>=100))throw new Error('Nie można wczytać pełnej listy archiwum.');
    if(cursor)seen.add(cursor);
  } while(cursor);
  return {[key]:items};
}
function cameraHealthText(camera){
  if(!camera.enabled)return 'Kamera wyłączona';
  const recording={unknown:'brak danych',waiting:'oczekiwanie',recording:'nagrywa',stale:'brak nowych plików',clock_skew:'nieprawidłowy czas nagrań',offline:'niedostępny',disabled:'wyłączony'};
  const stream={unknown:'brak danych',available:'dostępny',unavailable:'niedostępny'};
  const paused={disk_low:'wstrzymany - mało miejsca',disk_unavailable:'wstrzymany - niedostępny dysk'};
  return `Obraz: ${camera.stream_gateway_failed?'bramka niedostępna':(stream[camera.stream_health]||'brak danych')} | Zapis: ${paused[camera.recording_pause_reason]||recording[camera.health]||'brak danych'}`;
}
function view(which) {
  const cameraWasHidden=$('camera-view').hidden;
  if(which!=='archive'&&!$('archive-view').hidden){$('archive-object-overlay').hidden=true;nativeArchiveCache={camera:null,at:0,frames:[]};resetMotionSearch();playbackGeneration++;archiveLoadGeneration++;calendarGeneration++;archiveAutoRecovery=null;$('archive-calendar-panel').open=false;clearTimeout(previewTimer);$('timeline-preview').hidden=true;$('archive-player').pause();clearArchiveBuffers();}
  if(which!=='sync')stopSync();$('camera-view').hidden=which!=='cameras';$('archive-view').hidden=which!=='archive';$('sync-view').hidden=which!=='sync';$('admin-view').hidden=which!=='admin';$('settings-view').hidden=which!=='settings';
  for(const [id,active] of [['show-cameras',which==='cameras'],['show-archive',which==='archive'||which==='sync'],['show-sync',which==='sync'],['show-settings',which==='settings'],['show-admin',which==='admin']]){const tab=$(id);if(active)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');}
  if(which==='cameras'&&cameraWasHidden)renderCameras();
  else if(which!=='cameras'){
    stopTalkback();closeLiveMenu();stopLiveLayoutGesture();liveLayoutEditing=false;nativeTracks.clear();
    for(const player of $('cameras').querySelectorAll('video-stream'))closeLivePlayer(player);
    $('cameras').replaceChildren();
  }
}
async function refreshCameras() {
  const generation=++cameraRefreshGeneration,incoming=(await api('/api/cameras')).cameras;
  if(generation!==cameraRefreshGeneration)return;
  if(!Array.isArray(incoming))throw new Error('Nieprawidłowa lista kamer.');
  cameras=incoming;cameraListState='ready';
  // A layout endpoint failure must not prevent the actual camera inventory from appearing.
  const selected=$('layout-select').value;renderCameras();await refreshLayouts();const restoring=!workspaceReady||workspaceOwner!==me?.id;await restoreLiveWorkspace();if(generation===cameraRefreshGeneration&&(restoring||selected||$('layout-select').value))renderCameras();
}
async function refreshCameraStatus(){
  const generation=++cameraRefreshGeneration,incoming=(await api('/api/cameras')).cameras;
  if(generation!==cameraRefreshGeneration||$('camera-view').hidden)return;
  if(!Array.isArray(incoming))throw new Error('Nieprawidłowa lista kamer.');
  const shape=list=>JSON.stringify(list.map(c=>[c.id,c.name,c.slug,c.enabled,c.sub_configured,c.may_download]));
  const changed=shape(cameras)!==shape(incoming),wasReady=cameraListState==='ready';cameras=incoming;cameraListState='ready';
  if(changed){const selected=$('layout-select').value;renderCameras();await refreshLayouts();if(generation===cameraRefreshGeneration&&!$('camera-view').hidden&&(selected||$('layout-select').value))renderCameras();return;}
  if(!wasReady&&!cameras.length){renderCameras();return;}
  for(const card of $('cameras').querySelectorAll('.camera')){
    const camera=incoming.find(item=>String(item.id)===card.dataset.cameraId),label=card.querySelector('.health');
    if(camera&&label){updateLiveHealth(label,camera);const detail=card.querySelector('.live-health-detail');if(detail)detail.textContent=cameraHealthText(camera);}
  }
}
async function refreshLayouts(){
  const incoming=(await api('/api/layouts')).layouts;if(!Array.isArray(incoming))throw new Error('Nieprawidłowa lista układów kamer.');layouts=incoming;const select=$('layout-select'),old=select.value;
  select.replaceChildren(new Option('Wszystkie kamery',''));for(const layout of layouts)select.add(new Option(layout.name,String(layout.id)));select.value=layouts.some(layout=>String(layout.id)===old)?old:'';
  const manage=$('manage-layout-select'),managed=manage.value;manage.replaceChildren(new Option('Wybierz układ',''));for(const layout of layouts)manage.add(new Option(layout.name,String(layout.id)));manage.value=layouts.some(layout=>String(layout.id)===managed)?managed:'';$('delete-layout').disabled=!manage.value;
  const choices=$('layout-cameras');choices.replaceChildren();for(const camera of cameras){const label=element('label');const input=element('input');input.type='checkbox';input.value=String(camera.id);input.checked=true;label.append(input,document.createTextNode(camera.name));choices.append(label);}
}
function liveDiagnostics(native, previous, now){
  const ranges=native.buffered,time=native.currentTime;let ahead=null;
  if(ranges&&Number.isFinite(time))for(let i=0;i<ranges.length;i++){
    if(time>=ranges.start(i)&&time<=ranges.end(i)){ahead=ranges.end(i)-time;break;}
  }
  const gap=ranges&&ranges.length&&Number.isFinite(time)?Math.max(0,ranges.end(ranges.length-1)-time):null;
  const quality=typeof native.getVideoPlaybackQuality==='function'?native.getVideoPlaybackQuality():null;
  const total=quality&&Number.isFinite(quality.totalVideoFrames)?quality.totalVideoFrames:null;
  const dropped=quality&&Number.isFinite(quality.droppedVideoFrames)?quality.droppedVideoFrames:null;
  const frames=total!==null&&dropped!==null?Math.max(0,total-dropped):null;
  const fps=previous&&frames!==null&&previous.frames!==null&&frames>=previous.frames&&now>previous.now?(frames-previous.frames)*1000/(now-previous.now):null;
  return {now,frames,fps,ahead,gap,dropped};
}
function updateLiveHealth(label,camera){
  const full=cameraHealthText(camera);
  const recording=camera.enabled&&!camera.recording_pause_reason&&camera.health==='recording';
  const problem=camera.enabled&&(camera.stream_gateway_failed||camera.stream_health==='unavailable'||camera.recording_pause_reason||['stale','clock_skew','offline'].includes(camera.health));
  label.textContent=!camera.enabled?'Wył.':problem?'Uwaga':recording?'REC':camera.health==='waiting'?'Oczekuje':camera.health==='disabled'?'Bez zapisu':'Brak danych';
  label.dataset.state=problem?'warning':recording?'recording':'idle';
  label.title=full;label.setAttribute('aria-label',full);
}
let activeLiveMenu=null;
function closeLiveMenu(restoreFocus=false){
  if(!activeLiveMenu)return;
  const {card,trigger,panel}=activeLiveMenu;activeLiveMenu=null;
  panel.hidden=true;trigger.setAttribute('aria-expanded','false');card.classList.remove('live-menu-open');
  if(restoreFocus)trigger.focus();
}
if(typeof document.addEventListener==='function'){
  document.addEventListener('pointerdown',event=>{if(activeLiveMenu&&!activeLiveMenu.card.contains(event.target))closeLiveMenu();});
  document.addEventListener('focusin',event=>{if(activeLiveMenu&&!activeLiveMenu.card.contains(event.target))closeLiveMenu();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&activeLiveMenu){event.preventDefault();closeLiveMenu(true);}});
}
// Prefetch one camera at a time; do not starve Gunicorn or restart live players.
const deviceCache=new Map();let deviceQueue=Promise.resolve();
function loadDeviceInfo(camera,force=false,extended=false){
  const owner=me,key=String(owner?.id??owner?.username??owner?.role)+':'+camera.id+':'+camera.slug+(extended?':settings':':controls');
  const old=deviceCache.get(key);
  if(!force&&old&&(old.pending||Date.now()-old.time<120000))return old.promise;
  const entry={pending:true,time:Date.now()};
  entry.promise=deviceQueue.then(()=>{if(me!==owner||me?.role!=='admin')throw new Error('Sesja została zmieniona.');return api('/api/cameras/'+camera.id+(extended?'/settings':'/controls')+(force?'?refresh=1':''));});
  deviceQueue=entry.promise.catch(()=>{});deviceCache.set(key,entry);
  entry.promise.then(()=>{entry.pending=false;entry.time=Date.now();},()=>{entry.pending=false;entry.time=Date.now();});
  if(deviceCache.size>256){for(const [k,v] of deviceCache)if(!v.pending&&k!==key){deviceCache.delete(k);break;}}
  return entry.promise;
}

function invalidateDeviceInfo(camera){for(const key of deviceCache.keys())if(key.endsWith(':'+camera.id+':'+camera.slug+':settings')||key.endsWith(':'+camera.id+':'+camera.slug+':controls'))deviceCache.delete(key);}
function deviceControlPanel(camera){
  const panel=element('details',null,'device-controls');
  const summary=element('summary','Ustawienia sprzętowe kamery');panel.append(summary);const identity=element('p','Model: wykrywanie…','device-model');panel.append(identity);
  const status=element('p','');status.setAttribute('role','status');
  const fields=element('div',null,'device-control-fields');
  const refresh=element('button','Odśwież stan');refresh.type='button';
  panel.append(status,fields,refresh);
  const url='/api/cameras/'+camera.id+'/settings';let busy=false,loaded=false;
  function disable(value){refresh.disabled=value;for(const el of fields.querySelectorAll('button,input,select'))el.disabled=value;}
  async function send(control,value){
    if(busy)return;busy=true;disable(true);status.textContent='Wysyłanie polecenia…';
    try{await api(url,'POST',{control,value});invalidateDeviceInfo(camera);status.textContent='Kamera przyjęła polecenie. Odczytywanie stanu…';try{await read(true);}catch(error){status.textContent='Polecenie przyjęte; odczyt stanu nie powiódł się. '+error.message;}}
    catch(error){status.textContent=error.message;}
    finally{busy=false;disable(false);}
  }
  function button(label,control,value){const item=element('button',label);item.type='button';item.addEventListener('click',()=>send(control,value));return item;}
  function range(label,control,info){
    const group=element('div',null,'device-control-range');const caption=element('label',label+' · '+info.value);
    const slider=element('input');slider.type='range';slider.min=String(info.min);slider.max=String(info.max);slider.step='1';slider.value=String(info.value);slider.setAttribute('aria-label',label);
    slider.addEventListener('input',()=>{caption.textContent=label+' · '+slider.value;});
    const apply=element('button','Zastosuj');apply.type='button';apply.addEventListener('click',()=>send(control,Number(slider.value)));
    group.append(caption,slider,apply);return group;
  }
  async function read(force=false){
    const result=await loadDeviceInfo(camera,force,true);const c=result.controls||{};const settings=result.settings||[];fields.replaceChildren();
    const model=result.device?.model||'';const manufacturer=result.device?.manufacturer||'';
    identity.textContent=[manufacturer&& !model.toLowerCase().startsWith(manufacturer.toLowerCase())?manufacturer:'',model].filter(Boolean).join(' ')||'Model niedostępny przez API kamery';
    summary.textContent='Ustawienia sprzętowe kamery';
    if(model)summary.textContent+=' · '+model;
    if(result.device?.hardware)identity.textContent+=' · '+result.device.hardware;
    if(result.device?.firmware)identity.textContent+=' · FW '+result.device.firmware;
    if(result.device?.protocol)identity.textContent+=' · '+result.device.protocol;
    else if(result.device?.identity_source)identity.textContent+=' · '+result.device.identity_source;
    refresh.hidden=false;
    if(c.light_available===true&&typeof c.light!=='boolean')fields.append(button('Włącz reflektor','light',true),button('Wyłącz reflektor','light',false));
    if(typeof c.light==='boolean')fields.append(button(c.light?'Wyłącz reflektor':'Włącz reflektor','light',!c.light));
    if(Number.isInteger(c.brightness))fields.append(range('Jasność reflektora (%)','brightness',{value:c.brightness,min:0,max:100}));
    if(c.ir&&Array.isArray(c.ir.options)&&c.ir.options.length){
      const label=element('label','Podczerwień','device-select-row');const select=element('select');select.setAttribute('aria-label','Podczerwień');
      for(const mode of c.ir.options)select.add(new Option({Auto:'Automatycznie',Off:'Wyłączona',On:'Włączona'}[mode],mode));select.value=c.ir.value;
      const apply=element('button','Zastosuj');apply.type='button';apply.addEventListener('click',()=>send('ir',select.value));label.append(select,apply);fields.append(label);
    }
    if(c.zoom)fields.append(range('Zoom optyczny','zoom',c.zoom));if(typeof c.autofocus==='boolean')fields.append(button(c.autofocus?'Wyłącz autofocus':'Włącz autofocus','autofocus',!c.autofocus));if(c.focus)fields.append(range('Ostrość','focus',c.focus));
    if(Array.isArray(c.presets)&&c.presets.length){const label=element('label','Pozycja PTZ','device-select-row');const select=element('select');select.setAttribute('aria-label','Pozycja PTZ');for(const preset of c.presets)select.add(new Option(preset.name||'Pozycja '+preset.id,String(preset.id)));const go=element('button','Przejdź do pozycji');go.type='button';go.addEventListener('click',()=>send('preset',Number(select.value)));label.append(select,go);fields.append(label);}
    const groups=new Map();
    const translations={Auto:'Automatycznie',Color:'Kolor','Black&White':'Czarno-biały',On:'Włączona',Off:'Wyłączona',BackLightControl:'BLC',DynamicRangeControl:'WDR'};
    for(const info of settings){
      if(!groups.has(info.group)){const section=element('fieldset',null,'device-settings-group');section.append(element('legend',info.group));fields.append(section);groups.set(info.group,section);}
      const group=groups.get(info.group);
      if(info.type==='bool')group.append(button(info.label+': '+(info.value?'włączone':'wyłączone')+' (zmień)',info.key,!info.value));
      else if(info.type==='range')group.append(range(info.label,info.key,info));
      else if(info.type==='select'){const label=element('label',info.label,'device-select-row');const select=element('select');select.setAttribute('aria-label',info.label);for(const option of info.options)select.add(new Option(translations[option]||option,option));select.value=info.value;const apply=element('button','Zastosuj');apply.type='button';apply.addEventListener('click',()=>send(info.key,select.value));label.append(select,apply);group.append(label);}
    }
    loaded=true;
    status.textContent=(Object.keys(c).length||settings.length)?(result.partial?'Nie wszystkie funkcje odpowiedziały. Dostępne sterowanie poniżej.':'Stan odczytany z kamery.'):(result.partial?'Nie udało się odczytać możliwości kamery. Spróbuj ponownie.':'Kamera nie zgłasza obsługiwanych funkcji sterowania.');
    if(result.checked_at)status.textContent+=' Diagnoza: '+new Date(result.checked_at*1000).toLocaleString('pl-PL')+'.';
    refresh.textContent='Ponów diagnozę kamery';if(result.message){
      if(!Object.keys(c).length&&model)status.textContent='Brak potwierdzonych funkcji sterowania. '+result.message;
      else status.textContent=result.message;
    }
  }
  async function load(force=false){if(busy)return;busy=true;disable(true);status.textContent='Wykrywanie możliwości kamery…';try{await read(force);}catch(error){identity.textContent='Model niedostępny przez API kamery';status.textContent=error.message;}finally{busy=false;disable(false);}}
  refresh.addEventListener('click',()=>load(true));panel.addEventListener('toggle',()=>{if(panel.open&&!loaded)load();});return panel;
}
function liveLightControl(camera){
  const panel=element('div',null,'live-light');panel.hidden=true;
  let busy=false;const url='/api/cameras/'+camera.id+'/controls';
  function render(result){const c=result.controls||{};panel.replaceChildren();panel.hidden=typeof c.light!=='boolean'&&c.light_available!==true;if(panel.hidden)return;
    function add(label,value){const b=element('button',label);b.type='button';b.addEventListener('click',async()=>{if(busy)return;busy=true;for(const el of panel.querySelectorAll('button'))el.disabled=true;try{await api(url,'POST',{control:'light',value});invalidateDeviceInfo(camera);render(await loadDeviceInfo(camera,true));}catch(error){notify(error.message,'error');}finally{busy=false;for(const el of panel.querySelectorAll('button'))el.disabled=false;}});panel.append(b);}
    if(typeof c.light==='boolean')add(c.light?'Wyłącz LED':'Włącz LED',!c.light);else{add('Włącz LED',true);add('Wyłącz LED',false);}
  }
  loadDeviceInfo(camera).then(render,()=>{});return panel;
}
let activeTalkback=null;
function stopTalkback(session=activeTalkback,message=''){
  if(!session||activeTalkback!==session)return;
  activeTalkback=null;clearTimeout(session.timer);
  if(session.socket){session.socket.onclose=null;session.socket.onerror=null;try{session.socket.close();}catch{}}
  if(session.processor){session.processor.onaudioprocess=null;try{session.processor.disconnect();}catch{}}
  if(session.source)try{session.source.disconnect();}catch{}
  if(session.stream)for(const track of session.stream.getTracks())track.stop();
  if(session.context)void session.context.close().catch(()=>{});
  session.button.textContent='Mów do kamery';session.button.setAttribute('aria-pressed','false');
  if(message)notify(message,'error');
}
async function startTalkback(camera,button){
  if(activeTalkback?.button===button){stopTalkback();return;}
  stopTalkback();
  if(!globalThis.isSecureContext||!navigator.mediaDevices?.getUserMedia||!globalThis.AudioContext){notify('Mikrofon wymaga HTTPS oraz obsługi audio przez przeglądarkę.','error');return;}
  const session={button};activeTalkback=session;button.textContent='Zatrzymaj mikrofon';button.setAttribute('aria-pressed','true');
  session.timer=setTimeout(()=>stopTalkback(session,'Zakończono sesję mikrofonu.'),120000);
  try{
    const context=new AudioContext();session.context=context;await context.resume();
    if(activeTalkback!==session)return;
    const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    if(activeTalkback!==session){for(const track of stream.getTracks())track.stop();return;}
    session.stream=stream;
    for(const track of stream.getTracks())track.addEventListener('ended',()=>stopTalkback(session));
    const endpoint=new URL('/live/talk?src='+encodeURIComponent(camera.slug),location.origin);endpoint.protocol=endpoint.protocol==='https:'?'wss:':'ws:';
    const socket=new WebSocket(endpoint);session.socket=socket;
    socket.onclose=()=>stopTalkback(session,'Kanał mikrofonu został zamknięty.');
    socket.onerror=()=>stopTalkback(session,'Nie można otworzyć kanału głosowego kamery.');
    socket.onmessage=event=>{
      if(activeTalkback!==session)return;
      if(event.data!=='ready'){stopTalkback(session,'Kamera nie udostępnia zgodnego kanału głosowego ONVIF G.711.');return;}
      if(session.processor)return;
      try{
        const source=context.createMediaStreamSource(stream),processor=context.createScriptProcessor(1024,1,1),silent=context.createGain();
        session.source=source;session.processor=processor;silent.gain.value=0;
        // Downsample by averaging the input samples that contribute to each 8 kHz sample.
        let phase=0,total=0,count=0,offset=0,packet=new DataView(new ArrayBuffer(320));
        processor.onaudioprocess=e=>{
          if(activeTalkback!==session||socket.readyState!==WebSocket.OPEN)return;
          if(socket.bufferedAmount>32000){stopTalkback(session,'Zbyt duże opóźnienie kanału głosowego.');return;}
          for(const sample of e.inputBuffer.getChannelData(0)){
            total+=sample;count++;phase+=8000;
            if(phase<context.sampleRate)continue;
            phase-=context.sampleRate;const value=Math.max(-1,Math.min(1,total/count));total=0;count=0;
            packet.setInt16(offset,value<0?Math.round(value*32768):Math.round(value*32767),true);offset+=2;
            if(offset===320){socket.send(packet.buffer);offset=0;packet=new DataView(new ArrayBuffer(320));}
          }
        };
        source.connect(processor);processor.connect(silent);silent.connect(context.destination);
      }catch{stopTalkback(session,'Nie można przetworzyć sygnału mikrofonu.');}
    };
  }catch{stopTalkback(session,'Nie można uzyskać dostępu do mikrofonu.');}
}
function renderCameras() {
  stopTalkback();const area=$('cameras');closeLiveMenu();stopLiveLayoutGesture();for(const player of area.querySelectorAll('video-stream'))closeLivePlayer(player);area.replaceChildren();
  if($('camera-view').hidden)return;
  ensureLivePlayer();reconcileLiveLayout();
  const keys=new Set(cameras.filter(c=>c.enabled).map(c=>String(c.id)+':'+c.slug));
  for(const key of livePreferences.keys())if(!keys.has(key))livePreferences.delete(key);
  const layout=layouts.find(x=>String(x.id)===$('layout-select').value);const visible=layout?cameras.filter(c=>layout.cameras.includes(c.id)):cameras;
  $('camera-count').textContent=cameraListState==='ready'?String(visible.length):'';
  area.dataset.count=String(visible.length);
  for (const camera of visible) {
const card=element('article',null,'camera');card.dataset.cameraId=String(camera.id);const top=element('div',null,'camera-heading');
    top.append(element('strong',camera.name));
    const health=element('span',null,'health');updateLiveHealth(health,camera);top.append(health);
    const menu=element('button','Opcje','live-options-trigger');menu.type='button';menu.title='Opcje kamery';menu.setAttribute('aria-label','Opcje kamery '+camera.name);menu.setAttribute('aria-expanded','false');menu.setAttribute('aria-controls','live-options-'+camera.id);top.append(menu);card.append(top);
    const key=String(camera.id)+':'+camera.slug,previous=livePreferences.get(key)||{quality:savedLiveQuality(camera)||(camera.sub_configured?'sub':'main'),enlarged:false};
    let enlarged=Boolean(previous.enlarged&&camera.enabled),selected=previous.quality;
    if(!['sub','main'].includes(selected)||selected==='sub'&&!camera.sub_configured)selected=camera.sub_configured?'sub':'main';
    if(enlarged)card.classList.toggle('expanded');
    const remember=()=>livePreferences.set(key,{quality:selected,enlarged});
    function player(alias){const item=document.createElement('video-stream');item.mode='mse';item.media='video,audio';item.background=false;if(item.video)item.video.muted=true;watchNativeLiveControls(item);const overlay=element('div',null,'native-object-overlay');overlay.hidden=true;item.append(overlay);item.sunhillOverlay=overlay;const zoom=installDigitalZoom(item,()=>[item.video,overlay],()=>!liveLayoutEditing);item.addEventListener('loadedmetadata',zoom.apply,true);item.title='Rolka: zoom cyfrowy; przeciągnij: przesuń; dwuklik: rozmiar kafelka';item.addEventListener('dblclick',event=>{if(item!==video||!camera.enabled||document.fullscreenElement||liveLayoutGesture)return;event.preventDefault();event.stopPropagation();closeLiveMenu();toggleExpanded();});item.src=new URL('/live/api/ws?src='+encodeURIComponent(alias),location.origin);return item;}
    let video=null,currentAlias=null,compatibility=false;
    if(camera.enabled){if(livePlayerReady()){currentAlias=selectedLiveAlias(camera,selected,enlarged);video=player(currentAlias);card.append(video);}else card.append(element('p','Oczekiwanie na usługę podglądu.','camera-actions'));}else card.append(element('p','Podgląd wyłączony','camera-actions'));
    const actions=element('div',null,'camera-actions live-options');actions.hidden=true;actions.id='live-options-'+camera.id;actions.setAttribute('role','group');actions.setAttribute('aria-label','Opcje kamery '+camera.name);
    menu.addEventListener('click',()=>{
      const wasOpen=activeLiveMenu?.trigger===menu;closeLiveMenu();if(wasOpen)return;
      activeLiveMenu={card,trigger:menu,panel:actions};actions.hidden=false;menu.setAttribute('aria-expanded','true');card.classList.add('live-menu-open');
    });
    const quality=element('select');quality.setAttribute('aria-label','Jakość podglądu '+camera.name);if(camera.sub_configured)quality.add(new Option('Niski strumień','sub'));quality.add(new Option('Pełna jakość','main'));quality.value=selected;quality.hidden=!camera.enabled;
    const sound=element('button','Włącz dźwięk');sound.type='button';sound.hidden=!camera.enabled;
    let audioGeneration=0,audioPending=false,audioRequested=false;
    const audioPrompt=element('div',null,'live-audio-prompt');audioPrompt.hidden=true;audioPrompt.setAttribute('role','group');audioPrompt.setAttribute('aria-label','Dźwięk kamery '+camera.name);
    const audioMessage=element('p');audioMessage.setAttribute('role','status');
    const audioRetry=element('button','Włącz dźwięk');audioRetry.type='button';
    const audioDismiss=element('button','Pozostań bez dźwięku');audioDismiss.type='button';
    const audioControls=element('div',null,'live-audio-controls');audioControls.append(audioRetry,audioDismiss);audioPrompt.append(audioMessage,audioControls);
    function audioLabel(audible){sound.textContent=audible?'Wycisz':'Włącz dźwięk';sound.setAttribute('aria-pressed',String(audible));sound.setAttribute('aria-label',sound.textContent+' kamery '+camera.name);}
    audioLabel(false);
    function hideAudioPrompt(){audioPrompt.hidden=true;audioMessage.textContent='';}
    function showAudioPrompt(message){audioMessage.textContent=message;audioPrompt.hidden=false;}
    const diagnostics=element('details',null,'live-diagnostics');diagnostics.hidden=!camera.enabled;
    diagnostics.append(element('summary',globalThis.sunhillMsePolicy==='native'?'Diagnostyka podglądu - tryb fabryczny':'Diagnostyka podglądu'));
    const diagnosticText=element('p','Oczekiwanie na dane odtwarzacza.');diagnostics.append(diagnosticText);
    const compat=element('button','Tryb zgodności H.264');compat.type='button';compat.addEventListener('click',()=>{if(!video)return;compatibility=!compatibility;compat.textContent=compatibility?'Powróć do wybranej jakości':'Tryb zgodności H.264';replacePlayer(selectedLiveAlias(camera,compatibility?'compatible':selected,enlarged));});diagnostics.append(compat);
    function observeAudio(owner){
      if(!owner)return;
      suppressNativeLiveControls(owner);
      let previousSample=null,observedNative=null,initializedNative=null;
      const updateDiagnostics=event=>{
        if(owner!==video||!owner.video||event.target!==owner.video)return;
        const native=owner.video,now=performance.now();
        if(observedNative!==native){observedNative=native;previousSample=null;}
        if(event.type==='timeupdate'&&previousSample&&now-previousSample.now<1000)return;
        const sample=liveDiagnostics(native,previousSample,now);previousSample=sample;
        const number=(value,precision)=>value===null?'-':value.toFixed(precision);
        const mode=globalThis.sunhillMsePolicy==='smooth'?'płynny MSE':'fabryczny MSE (poprawka niedostępna)';
        const state=event.type==='waiting'||event.type==='stalled'?'buforowanie':native.paused?'pauza':'odtwarzanie';
        diagnosticText.textContent=mode+' | '+state+' | '+(native.videoWidth||0)+'×'+(native.videoHeight||0)+' | FPS '+number(sample.fps,1)+' | utracone '+number(sample.dropped,0)+' | bufor '+number(sample.ahead,1)+' s | do końca bufora '+number(sample.gap,1)+' s | '+number(Number.isFinite(native.playbackRate)?native.playbackRate:null,2)+'×';
      };
      for(const type of ['timeupdate','playing','waiting','stalled'])owner.addEventListener(type,updateDiagnostics,true);
      const sync=event=>{
        if(owner!==video||!owner.video||event.target!==owner.video)return;
        suppressNativeLiveControls(owner);
        if(event.type==='loadedmetadata'&&initializedNative!==owner.video){
          if(initializedNative){audioGeneration++;audioPending=false;audioRequested=false;}
          initializedNative=owner.video;
          if(!audioRequested)owner.video.muted=true;
        }
        const audible=!owner.video.muted&&owner.video.volume!==0;audioLabel(audible);if(Number.isFinite(owner.video.volume))volume.value=String(owner.video.volume);
        if(audible&&!audioPending)hideAudioPrompt();
        if(event.type==='loadedmetadata')updateDiagnostics(event);
      };
      // Media events do not bubble, but the native video is a child of the
      // player. Capture browser/native control changes and reconnections.
      owner.addEventListener('volumechange',sync,true);
      owner.addEventListener('loadedmetadata',sync,true);
    }
    observeAudio(video);
    async function setAudio(enable){
      const owner=video,native=owner&&owner.video;
      if(!native){showAudioPrompt('Odtwarzacz nie jest jeszcze gotowy. Spróbuj ponownie, gdy pojawi się obraz.');return;}
      const generation=++audioGeneration;audioPending=enable;audioRequested=enable;
      const current=()=>generation===audioGeneration&&video===owner&&owner.video===native&&card.isConnected!==false;
      hideAudioPrompt();
      if(enable&&native.volume===0)native.volume=1;
      native.muted=!enable;audioLabel(enable);
      if(!enable){audioPending=false;return;}
      try{
        // No asynchronous work before play(): retain the browser user gesture.
        await native.play();
        if(current())audioPending=false;
        if(current()&&(native.muted||native.volume===0)){audioLabel(false);showAudioPrompt('Dźwięk nadal jest wyciszony. Spróbuj ponownie lub sprawdź ustawienia odtwarzania tej witryny.');}
      }catch(error){
        // An older rejected play() must not mute a newer user choice or player.
        if(!current())return;
        audioPending=false;audioRequested=false;native.muted=true;audioLabel(false);
        showAudioPrompt(error&&error.name==='NotAllowedError'?'Przeglądarka zablokowała dźwięk. Kliknij Włącz dźwięk. Jeśli blokada się powtórzy, sprawdź ustawienia automatycznego odtwarzania tej witryny.':'Nie udało się uruchomić dźwięku. Spróbuj ponownie. Jeśli problem pozostanie, sprawdź strumień audio kamery.');
        // Preserve muted video after a rejected audible play, without reconnecting.
        if(native.paused){try{await native.play();}catch{/* Existing player retry remains available. */}}
      }
    }
    sound.addEventListener('click',()=>{const native=video&&video.video;return setAudio(!native||native.muted||native.volume===0);});
    audioRetry.addEventListener('click',()=>setAudio(true));
    audioDismiss.addEventListener('click',async()=>{
      const generation=++audioGeneration,owner=video,native=owner&&owner.video;audioPending=false;audioRequested=false;
      if(native)native.muted=true;audioLabel(false);hideAudioPrompt();
      if(native&&native.paused){try{await native.play();}catch{
        if(generation===audioGeneration&&video===owner&&owner.video===native&&card.isConnected!==false)showAudioPrompt('Nie udało się wznowić obrazu bez dźwięku. Użyj Połącz ponownie.');
      }}
    });
    function replacePlayer(alias){if(!video)return;const replacement=player(alias);audioGeneration++;audioPending=false;audioRequested=false;diagnosticText.textContent='Oczekiwanie na dane odtwarzacza.';closeLivePlayer(video);video.replaceWith(replacement);video=replacement;currentAlias=alias;audioLabel(false);hideAudioPrompt();observeAudio(replacement);}
    function changeQuality(){selected=quality.value;remember();saveLiveQuality(camera,selected);compatibility=false;compat.textContent='Tryb zgodności H.264';if(!video)return;const alias=selectedLiveAlias(camera,selected,enlarged);if(alias!==currentAlias)replacePlayer(alias);}
    quality.addEventListener('change',changeQuality);actions.append(quality,sound);
    const retry=element('button','Połącz ponownie');retry.type='button';retry.hidden=!camera.enabled;retry.addEventListener('click',()=>{if(video)replacePlayer(currentAlias);else{ensureLivePlayer();if(livePlayerReady())renderCameras();}});actions.append(retry);
    function toggleExpanded(){enlarged=card.classList.toggle('expanded');remember();}
    const button=element('button','Archiwum');
    button.addEventListener('click',()=>{closeLiveMenu();return openArchive(camera);});actions.append(button);
    const volume=element('input');volume.type='range';volume.min='0';volume.max='1';volume.step='0.05';volume.value='1';volume.setAttribute('aria-label','Głośność kamery '+camera.name);
    const volumeLabel=element('label','Głośność','live-volume');volumeLabel.hidden=!camera.enabled;volumeLabel.append(volume);actions.append(volumeLabel);
    volume.addEventListener('input',()=>{if(video?.video)video.video.volume=Number(volume.value);});
    const full=element('button','Pełny ekran');full.type='button';full.hidden=!camera.enabled;full.addEventListener('click',()=>{closeLiveMenu();const native=video?.video;if(document.fullscreenElement){document.exitFullscreen?.();}else if(card.requestFullscreen){card.requestFullscreen().catch(()=>{if(native?.webkitEnterFullscreen)native.webkitEnterFullscreen();else notify('Pełny ekran niedostępny.');});}else if(native?.webkitEnterFullscreen)native.webkitEnterFullscreen();});actions.append(full);
    if(camera.enabled&&me&&me.role==='admin'){actions.append(liveLightControl(camera));const talk=element('button','Mów do kamery');talk.type='button';talk.hidden=true;loadDeviceInfo(camera).then(result=>{talk.hidden=result.talkback!==true;},()=>{});talk.setAttribute('aria-pressed','false');talk.addEventListener('click',()=>startTalkback(camera,talk));actions.append(talk);}
    actions.append(element('p',cameraHealthText(camera),'live-health-detail'),diagnostics);card.append(actions,audioPrompt);installLiveLayoutHandles(card,camera);area.append(card);
  }
  if(!visible.length){
    const empty=element('div',null,'empty-state');
    if(cameraListState==='loading'){empty.append(element('h2','Wczytywanie kamer…'),element('p','Trwa pobieranie listy kamer.'));}
    else if(cameraListState==='error'){empty.append(element('h2','Nie udało się pobrać kamer'),element('p','Użyj przycisku Odśwież, aby ponowić próbę.'));}
    else if(!cameras.length){empty.append(element('h2','Brak dostępnych kamer'),element('p',me&&me.role==='admin'?'Dodaj kamerę w sekcji Administracja.':'Żadna kamera nie jest dostępna dla tego konta. Skontaktuj się z administratorem, aby sprawdzić przypisane uprawnienia.'));}
    else{empty.append(element('h2','Brak kamer w tym układzie'),element('p','Wybierz widok Wszystkie kamery lub zmień układ w ustawieniach.'));}
    area.append(empty);
  }
  applyLiveLayout();
}
async function openArchive(camera, date=null, event=null) {
  exportViewGeneration++;if(exportTimer!==null)clearInterval(exportTimer);exportTimer=null;exportSubmitting=false;protectionSubmitting=false;
  clearTimeout(previewTimer);$('timeline-preview').hidden=true;
  clearTimelineSelection();
  activeCamera=camera;const selector=$('archive-camera-select');selector.replaceChildren();for(const item of cameras)selector.add(new Option(item.name,String(item.id)));selector.value=String(camera.id);$('archive-selection').hidden=!camera.may_download;resetArchiveContents();loadedArchiveDate=null;$('archive-title').textContent='Archiwum - '+camera.name;
  playbackGeneration++;resetMotionSearch();$('archive-object-overlay').hidden=true;archiveDigitalZoom.reset();$('archive-date').value=date||cameraDay();$('archive-timezone').textContent='Strefa czasowa kamer: '+me.timezone;$('archive-player').pause();$('archive-player').removeAttribute('src');if($('archive-player').load)$('archive-player').load();
  $('archive-month').value=$('archive-date').value.slice(0,7);$('calendar-days').replaceChildren();$('archive-calendar-panel').open=false;$('save-frame').hidden=!camera.may_download;$('export-panel').hidden=!camera.may_download;$('export-result').replaceChildren();$('archive-player').muted=true;updateArchiveControls();view('archive');const loaded=await loadArchive(!event);if(loaded&&event&&activeCamera===camera&&!$('archive-view').hidden&&$('archive-date').value===date)seekToEvent(event);
}
async function refreshExports(){
  if(exportListPending){exportListRefreshAgain=true;return;}
  exportListPending=true;
  const generation=++exportListGeneration;
  try{
    const result=await api('/api/export-jobs');
    if(generation!==exportListGeneration)return;
    const box=$('exports-list');box.replaceChildren();
    const labels={queued:'W kolejce',running:'Przetwarzanie',done:'Gotowe',failed:'Błąd'};
    for(const job of result.jobs){
      const row=element('div',null,'recording-row');
      const percent=job.status==='running'?' - '+Math.round(Math.max(0,Math.min(1,Number(job.progress)||0))*100)+'%':'';
      const created=new Intl.DateTimeFormat('pl-PL',{timeZone:me.timezone,dateStyle:'short',timeStyle:'medium'}).format(new Date(job.created*1000));
      row.append(element('span',`${job.camera} | ${created} | ${job.cancel_requested?'Anulowanie':job.cancelled?'Anulowano':labels[job.status]||job.status}${percent}`));
      if(job.download_ready){const link=element('a','Pobierz MP4');link.href=`/api/export-jobs/${job.id}/file`;row.append(link);}
      if(job.status==='failed'&&job.error)row.append(element('span',job.error));
      if((job.status==='queued'||job.status==='running')&&!job.cancel_requested){
        const cancel=element('button','Anuluj eksport');cancel.type='button';
        cancel.addEventListener('click',async()=>{if(!window.confirm('Anulować ten eksport?'))return;cancel.disabled=true;try{await api(`/api/export-jobs/${job.id}/cancel`,'POST',{});await refreshExports();}catch(error){cancel.disabled=false;notify(error.message,'error');}});row.append(cancel);
      }
      if(job.status!=='running'){
        const remove=element('button',job.status==='queued'?'Usuń zadanie':'Usuń eksport');remove.type='button';
        remove.addEventListener('click',async()=>{if(!window.confirm('Usunąć zadanie i jego gotowy plik eksportu?'))return;remove.disabled=true;try{await api(`/api/export-jobs/${job.id}`,'DELETE');await refreshExports();}catch(error){remove.disabled=false;notify(error.message,'error');}});row.append(remove);
      }
      box.append(row);
    }
    if(!result.jobs.length)box.append(element('p','Brak eksportów.'));
  }catch(error){notify(error.message,'error');}
  finally{exportListPending=false;if(exportListRefreshAgain){exportListRefreshAgain=false;refreshExports();}}
}
$('exports-refresh').addEventListener('click',refreshExports);
setInterval(()=>{if(me&&!document.hidden&&!$('settings-view').hidden)refreshExports();},5000);
function syncArchiveDateLabel(){
  const day=$('archive-date').value;$('archive-date-label').textContent=day?day.split('-').reverse().join('.'):'Wybierz datę';
}
function renderCalendar(month,days){
  const box=$('calendar-days');box.replaceChildren();
  const match=/^(\d{4})-(\d{2})$/.exec(month);if(!match)return;
  const year=Number(match[1]),m=Number(match[2]);if(year<1970||year>2099||m<1||m>12)return;
  const first=new Date(Date.UTC(year,m-1,1)),count=new Date(Date.UTC(year,m,0)).getUTCDate(),available=new Set(days);
  for(let n=0;n<(first.getUTCDay()+6)%7;n++)box.append(element('span',null,'archive-calendar-blank'));
  for(let n=1;n<=count;n++){
    const day=month+'-'+String(n).padStart(2,'0'),enabled=available.has(day),button=element('button',String(n),enabled?'archive-day available':'archive-day');button.type='button';button.disabled=!enabled;
    button.setAttribute('aria-label',day+(enabled?' - nagrania dostępne':' - brak nagrań'));
    if(day===$('archive-date').value)button.setAttribute('aria-current','date');
    if(enabled)button.addEventListener('click',()=>{if(button.disabled)return;$('archive-date').value=day;syncArchiveDateLabel();$('archive-calendar-panel').open=false;loadArchive(true);});
    box.append(button);
  }
}
async function loadCalendar(){
  const generation=++calendarGeneration,camera=activeCamera,month=$('archive-month').value;
  renderCalendar(month,[]);if(!camera)return;
  $('archive-calendar-status').textContent='Wczytywanie dostępnych dni…';
  const current=()=>generation===calendarGeneration&&camera===activeCamera&&month===$('archive-month').value;
  try{
    const result=await api(`/api/cameras/${camera.id}/calendar?month=${encodeURIComponent(month)}`);
    if(!current())return;
    renderCalendar(month,result.days);$('archive-calendar-status').textContent=result.days.length?'Podświetlone dni zawierają nagrania.':'Brak nagrań w wybranym miesiącu.';
  }catch(error){if(current())$('archive-calendar-status').textContent='Nie udało się pobrać dni: '+error.message;}
}
function changeArchiveMonth(delta){
  const value=$('archive-month').value,parts=value.split('-').map(Number);if(parts.length!==2||!parts.every(Number.isFinite))return;
  const target=new Date(Date.UTC(parts[0],parts[1]-1+delta,1)).toISOString().slice(0,7);if(target<'1970-01'||target>'2099-12')return;
  $('archive-month').value=target;loadCalendar();
}
$('load-calendar').addEventListener('click',loadCalendar);
$('archive-month').addEventListener('change',loadCalendar);
$('archive-month-prev').addEventListener('click',()=>changeArchiveMonth(-1));
$('archive-month-next').addEventListener('click',()=>changeArchiveMonth(1));
$('archive-calendar-panel').addEventListener('keydown',event=>{if(event.key==='Escape'){$('archive-calendar-panel').open=false;$('archive-date-label').focus();}});
function resetArchiveContents(){
  clearArchiveBuffers();archiveAutoRecovery=null;setArchiveNote('');
  records=[];eventMarkers=[];recordPosition=0;archiveRecordId=null;archiveRequestedDate=null;archiveScrubbing=false;
  $('timeline-track').replaceChildren();$('events').replaceChildren();if($('archive-detection-overlay'))$('archive-detection-overlay').hidden=true;
  $('timeline-position').value='0';$('timeline-time').textContent='00:00:00';$('archive-status').textContent='';$('archive-events-status').textContent='';
  updateArchiveControls();
}
function updateArchiveControls(){
  const player=$('archive-player'),record=archiveRecordId===null?null:records.find(item=>item.id===archiveRecordId);
  $('archive-play').disabled=!record||Boolean(archiveSwitch);$('archive-play').textContent=player.paused===false?'Pauza':'Odtwórz';
  $('archive-audio').textContent=player.muted?'Włącz dźwięk':'Wycisz';$('archive-audio').setAttribute('aria-pressed',String(!player.muted));
  $('archive-file-actions').hidden=!(record&&activeCamera&&activeCamera.may_download);
  if(record&&activeCamera&&activeCamera.may_download){$('archive-download').href=`/api/recordings/${record.id}/file?download=1`;$('archive-protect').textContent=record.protected?'Usuń ochronę':'Chroń nagranie';}
  else $('archive-download').removeAttribute('href');
}
async function loadArchive(autoStart=false) {
  syncArchiveDateLabel();
  const generation=++archiveLoadGeneration,date=$('archive-date').value,camera=activeCamera;
  if(!camera)return false;
  const player=$('archive-player');
  if(loadedArchiveDate!==null?loadedArchiveDate!==date:archiveRequestedDate!==null&&archiveRequestedDate!==date){
    clearTimelineSelection();resetMotionSearch();playbackGeneration++;player.pause();player.removeAttribute('src');if(player.load)player.load();resetArchiveContents();loadedArchiveDate=null;
  }
  archiveRequestedDate=date;archiveScrubbing=false;
  const current=()=>generation===archiveLoadGeneration&&camera===activeCamera&&date===$('archive-date').value;
  $('archive-status').textContent='Wczytywanie nagrań…';notify('');
  try {
    const recordingData=await archivePages(`/api/cameras/${camera.id}/recordings?date=${encodeURIComponent(date)}`,'recordings',current);
    if(!current())return false;
    // Keep playback attached to the recording ID even before loadedmetadata.
    const playingId=loadedArchiveDate===date?(archiveRecordId??(player.currentSrc?records[recordPosition]?.id:null)):null;
    records=recordingData.recordings;loadedArchiveDate=date;
    if(playingId!=null){
      const position=records.findIndex(record=>record.id===playingId);
      if(position>=0){recordPosition=position;archiveRecordId=playingId;}
      else{playbackGeneration++;clearArchiveBuffers();player.pause();player.removeAttribute('src');if(player.load)player.load();recordPosition=0;archiveRecordId=null;}
    }
    if(autoStart===true&&archiveRecordId===null){
      const playable=records.map((record,index)=>({record,index})).filter(({record})=>record.utc_first&&!record.utc_second);
      const first=date===cameraDay()?playable[playable.length-1]:playable[0];
      if(first){archiveAutoRecovery={camera,date,remaining:2,failures:[]};const spans=timelineSpans(first.record);const offset=spans.length?Math.max(0,spans[0][0]-seconds(first.record.local_time)):0;playRecord(first.index,offset,true);}
    }
    if(archiveRecordId===null)$('archive-status').textContent=records.length?'Wybierz czas nagrania na osi.':'Brak nagrań w wybranym dniu.';
    else if(playingId===archiveRecordId)$('archive-status').textContent='';
    renderTimeline();renderEvents();updateArchiveControls();
    // Events are optional metadata. Never delay the video for them or the calendar.
    $('archive-events-status').textContent='Wczytywanie zdarzeń…';
    void archivePages(`/api/cameras/${camera.id}/events?date=${encodeURIComponent(date)}`,'events',current).then(data=>{
      if(!current())return;eventMarkers=data.events;renderTimeline();renderEvents();$('archive-events-status').textContent=eventMarkers.length?'':'Brak zdarzeń.';
    }).catch(error=>{if(current())$('archive-events-status').textContent='Nie udało się pobrać zdarzeń: '+error.message;});
    return true;
  }catch(e){if(current()){$('archive-status').textContent=e.message;notify(e.message);}return false;}
}
// Keep the current decoded frame visible until the target MP4 is ready. At most
// two neighbouring segments are prefetched, only near their boundaries.
const archiveBuffers=new Map();
let archiveSwitch=null;
function archiveSource(id){return `/api/recordings/${id}/file`+(archiveCompatCameras.has(activeCamera?.id)?'?compat=hvc1':'');}
function disposeArchiveBuffer(id){
  const item=archiveBuffers.get(id);if(!item)return;
  archiveBuffers.delete(id);item.video.pause();item.video.removeAttribute('src');item.video.load();item.video.remove();
}
function clearArchiveBuffers(){
  if(archiveSwitch){archiveSwitch.cancel();archiveSwitch=null;}
  for(const id of archiveBuffers.keys())disposeArchiveBuffer(id);
}
function canBufferArchive(){return typeof HTMLVideoElement!=='undefined'&&$('archive-player') instanceof HTMLVideoElement&&Boolean($('archive-stage'));}
function prepareArchiveBuffer(record,offset){
  let item=archiveBuffers.get(record.id);
  if(!item){
    const video=document.createElement('video');video.className='archive-media archive-standby';video.playsInline=true;video.muted=true;video.preload='auto';video.tabIndex=-1;video.setAttribute('aria-hidden','true');
    item={video,offset};archiveBuffers.set(record.id,item);bindArchiveMedia(video);
    video.addEventListener('loadedmetadata',()=>{if(archiveBuffers.get(record.id)===item)try{video.currentTime=item.offset;}catch{}});
    $('archive-stage').append(video);video.src=archiveSource(record.id);
  }else if(Math.abs(item.offset-offset)>0.1){item.offset=offset;if(item.video.readyState>=1)try{item.video.currentTime=offset;}catch{}}
  return item;
}
function warmArchiveNeighbours(){
  if(!canBufferArchive()||archiveSwitch||$('archive-view').hidden)return;
  const player=$('archive-player'),record=records[recordPosition];if(!record||archiveRecordId!==record.id||!player.currentSrc.endsWith(archiveSource(record.id))||player.readyState<2||!record.utc_first||record.utc_second)return;
  const wanted=new Map(),t=player.currentTime;
  const prev=records[recordPosition-1],next=records[recordPosition+1];
  if(t<=65&&prev?.utc_first&&!prev.utc_second)wanted.set(prev.id,[prev,Math.max(0,prev.duration-10)]);
  if(record.duration-t<=65&&next?.utc_first&&!next.utc_second)wanted.set(next.id,[next,0]);
  for(const id of archiveBuffers.keys())if(!wanted.has(id))disposeArchiveBuffer(id);
  for(const [id,[candidate,offset]] of wanted)if(!archiveBuffers.has(id))prepareArchiveBuffer(candidate,offset);
}
function setArchiveNote(message){const note=$('archive-playback-note');note.textContent=message;note.hidden=!message;}
function archiveMediaMessage(error){
  if(error?.name==='NotAllowedError')return 'Przeglądarka wymaga kliknięcia Odtwórz.';
  switch(error?.code){case 1:return 'Odtwarzanie zostało przerwane.';case 2:return 'Błąd pobierania nagrania.';case 3:return 'Przeglądarka nie może zdekodować nagrania.';case 4:return 'Przeglądarka nie obsługuje źródła lub kodeka nagrania.';default:return error?.name==='NotSupportedError'?'Przeglądarka nie obsługuje źródła lub kodeka nagrania.':'Nie udało się odtworzyć nagrania.';}
}
async function diagnoseArchiveFailure(record,error,generation){
  if(generation!==playbackGeneration||archiveErrorGeneration===generation)return;
  archiveErrorGeneration=generation;
  const camera=activeCamera,date=$('archive-date').value;
  const current=()=>generation===playbackGeneration&&camera===activeCamera&&date===$('archive-date').value;
  const base=archiveMediaMessage(error),label='Nagranie #'+record.id+' ('+record.local_time?.replace('T',' ')+')';
  $('archive-status').textContent=base+' Sprawdzanie pliku…';
  let detail='',needsCompatibility=false;
  const intent=archivePlaybackIntent;
  const compatible=archiveCompatCameras.has(camera?.id);
  try{
    const head=await fetch(archiveSource(record.id),{method:'HEAD',credentials:'same-origin',cache:'no-store'});
    if(!head.ok)detail='HTTP '+head.status+' - serwer nie udostępnił pliku.';
    else if(!head.headers.get('Content-Type')?.includes('video/mp4'))detail='Serwer nie zwrócił MP4.';
    else{
      const info=await api(`/api/recordings/${record.id}/media-info`);
      needsCompatibility=info.streams.some(item=>item.type==='video'&&item.codec==='hevc'&&item.tag!=='hvc1');
      detail=info.streams.map(item=>`${item.type}: ${item.codec}${item.tag?' ('+item.tag+')':''}`).join(', ');
      if(detail)detail='HTTP '+head.status+'; '+detail+'.';
    }
  }catch(e){detail='Diagnostyka pliku: '+e.message;}
  if(!current())return;
  if(!compatible&&needsCompatibility&&camera&&(error?.code===3||error?.code===4||error?.name==='NotSupportedError')){
    const index=records.findIndex(item=>item.id===record.id);
    if(index>=0){
      archiveCompatCameras.add(camera.id);clearArchiveBuffers();
      const offset=intent?.generation===generation?intent.offset:0;
      playRecord(index,offset,intent?.automatic||false,intent?.keepPaused||false);
      setArchiveNote('Przygotowywanie zgodnego nagrania HEVC (hvc1), bez rekompresji.');
      return;
    }
  }
  const message=label+': '+base+(detail?' '+detail:'')+(compatible?' Próba zgodności hvc1 nie powiodła się.':'');
  const recovery=archiveAutoRecovery;
  if(recovery&&recovery.camera===camera&&recovery.date===date&&recovery.remaining>0){
    const index=records.findIndex(item=>item.id===record.id);let previous=index-1;
    while(previous>=0&&(!records[previous].utc_first||records[previous].utc_second))previous--;
    if(previous>=0){recovery.remaining--;recovery.failures.push(message);setArchiveNote(recovery.failures.join(' | ')+' Automatyczna próba odtworzenia wcześniejszego segmentu.');playRecord(previous,0,true);return;}
  }
  archiveAutoRecovery=null;setArchiveNote(message);$('archive-status').textContent=archiveRecordId===record.id?'Wybierz inny czas lub kliknij Odtwórz, aby ponowić próbę.':'Poprzedni fragment pozostaje dostępny. Wybierz czas ponownie.';updateArchiveControls();
}
function playRecord(index,offset=0,automatic=false,keepPaused=false) {
  if($('archive-detection-overlay'))$('archive-detection-overlay').hidden=true;
  const record=records[index];if(!record)return;
  if(!automatic){archiveAutoRecovery=null;setArchiveNote('');}
  if(archiveSwitch){archiveSwitch.cancel();archiveSwitch=null;}
  const player=$('archive-player'),generation=++playbackGeneration,source=archiveSource(record.id);
  archivePlaybackIntent={generation,offset,automatic,keepPaused};
  if(record.utc_first&&!record.utc_second){const instant=Date.parse(record.utc_first)/1000+offset;if(cameraDay(instant*1000)===$('archive-date').value){const position=eventSeconds(instant);$('timeline-position').value=String(position);$('timeline-time').textContent=clock(position);}}
  const commit=(target)=>{
    if(generation!==playbackGeneration)return;
    recordPosition=records.findIndex(item=>item.id===record.id);if(recordPosition<0)return;
    archiveRecordId=record.id;target.playbackRate=Number($('playback-speed').value)||1;
    if(automatic)target.muted=true;
    if(!keepPaused)target.play().catch(error=>{if(generation!==playbackGeneration)return;if(error.name==='NotAllowedError'){const wasMuted=target.muted;target.muted=true;updateArchiveControls();const prompt=()=>{if(generation===playbackGeneration)$('archive-status').textContent='Kliknij Odtwórz, aby rozpocząć odtwarzanie.';};if(wasMuted)prompt();else target.play().catch(prompt);}else if(error.name!=='AbortError')void diagnoseArchiveFailure(record,target.error||error,generation);});
    else {$('archive-status').textContent='';target.pause();}
    updateArchiveControls();warmArchiveNeighbours();
  };
  // Initial load and same-file seek use the existing element; no redundant reload.
  if(!canBufferArchive()||!player.currentSrc||player.error||player.readyState<2||player.currentSrc.endsWith(source)){
    recordPosition=index;archiveRecordId=record.id;
    $('archive-status').textContent='Wczytywanie obrazu…';if(automatic)player.muted=true;
    const start=()=>{if(generation!==playbackGeneration)return;player.currentTime=offset;commit(player);};
    if(player.currentSrc&&player.currentSrc.endsWith(source)&&player.readyState>=1&&!player.error)start();
    else{clearArchiveBuffers();player.addEventListener('loadedmetadata',start,{once:true});player.src=source;player.load?.();}
    updateArchiveControls();return;
  }
  // Do not change the active record or its actions until the replacement is ready.
  const muted=player.muted,volume=player.volume;player.pause();
  for(const id of archiveBuffers.keys())if(id!==record.id)disposeArchiveBuffer(id);
  const item=prepareArchiveBuffer(record,offset),target=item.video;
  $('archive-status').textContent='Przygotowywanie fragmentu…';
  let timer,finished=false;
  const cleanup=()=>{clearTimeout(timer);for(const event of ['loadeddata','canplay','seeked'])target.removeEventListener(event,ready);target.removeEventListener('error',fail);};
  const cancel=()=>{if(finished)return;finished=true;cleanup();};
  const fail=()=>{if(finished)return;const error=target.error;cancel();archiveSwitch=null;disposeArchiveBuffer(record.id);$('archive-status').textContent='Nie udało się przygotować fragmentu. Poprzedni obraz pozostaje dostępny.';updateArchiveControls();if(error)void diagnoseArchiveFailure(record,error,generation);};
  const ready=()=>{
    if(finished||generation!==playbackGeneration)return;
    if(target.readyState<2||target.seeking||Math.abs(target.currentTime-offset)>0.2)return;
    if(!records.some(item=>item.id===record.id)){fail();return;}
    cancel();archiveSwitch=null;archiveBuffers.delete(record.id);
    const focused=document.activeElement===player;
    player.removeAttribute('id');player.classList.add('archive-standby');player.pause();
    target.id='archive-player';target.classList.remove('archive-standby');archiveDigitalZoom.apply();target.removeAttribute('aria-hidden');target.tabIndex=0;target.setAttribute('aria-label','Odtwarzacz archiwum');target.volume=volume;target.muted=automatic||muted;
    player.removeAttribute('src');player.load();player.remove();if(focused)target.focus();
    $('archive-status').textContent='';commit(target);
  };
  archiveSwitch={cancel,target:Date.parse(record.utc_first)/1000+offset,paused:keepPaused};updateArchiveControls();
  for(const event of ['loadeddata','canplay','seeked'])target.addEventListener(event,ready);target.addEventListener('error',fail);
  timer=setTimeout(fail,source.includes('?compat=hvc1')?50000:15000);if(target.error)fail();else ready();
}
$('archive-calendar-panel').addEventListener('toggle',()=>{if($('archive-calendar-panel').open&&activeCamera){$('archive-month').value=$('archive-date').value.slice(0,7);loadCalendar();}});
$('archive-play').addEventListener('click',()=>{const player=$('archive-player'),record=records.find(item=>item.id===archiveRecordId);if(!record)return;archiveAutoRecovery=null;if(player.error||player.readyState===0){playRecord(records.indexOf(record),Number.isFinite(player.currentTime)?player.currentTime:0);return;}if(player.paused!==false){const generation=playbackGeneration;player.play().catch(error=>{if(generation!==playbackGeneration)return;if(error.name==='NotAllowedError')$('archive-status').textContent=archiveMediaMessage(error);else if(error.name!=='AbortError')void diagnoseArchiveFailure(record,player.error||error,generation);});}else player.pause();});
$('archive-audio').addEventListener('click',()=>{const player=$('archive-player');player.muted=!player.muted;updateArchiveControls();});
$('archive-volume').addEventListener('input',event=>{const player=$('archive-player');player.volume=Number(event.target.value);player.muted=player.volume===0;updateArchiveControls();});
function toggleArchiveFullscreen(){const stage=$('archive-stage'),player=$('archive-player');try{if(document.fullscreenElement===stage||document.webkitFullscreenElement===stage){const exit=document.exitFullscreen||document.webkitExitFullscreen;Promise.resolve(exit.call(document)).catch(()=>notify('Nie udało się zamknąć pełnego ekranu.'));return;}const request=stage.requestFullscreen||stage.webkitRequestFullscreen;if(request){Promise.resolve(request.call(stage)).catch(()=>notify('Pełny ekran niedostępny.'));}else if(player.webkitEnterFullscreen)player.webkitEnterFullscreen();else notify('Pełny ekran niedostępny.');}catch{notify('Pełny ekran niedostępny.');}}
$('archive-fullscreen').addEventListener('click',toggleArchiveFullscreen);
$('archive-exit-fullscreen').addEventListener('click',toggleArchiveFullscreen);
$('archive-protect').addEventListener('click',async()=>{const record=records.find(item=>item.id===archiveRecordId),camera=activeCamera,date=$('archive-date').value,button=$('archive-protect');if(!record||!camera?.may_download||button.disabled)return;button.disabled=true;try{await api(`/api/recordings/${record.id}/protection`,record.protected?'DELETE':'POST',record.protected?null:{});if(camera===activeCamera&&date===$('archive-date').value)await loadArchive();}catch(e){if(camera===activeCamera&&date===$('archive-date').value)notify(e.message);}finally{button.disabled=false;}});
function bindArchiveMedia(video){
  const active=()=>video===$('archive-player');
  video.addEventListener('click',()=>{if(active()&&archiveRecordId!==null)$('archive-play').click();});
  video.addEventListener('keydown',event=>{if(active()&&(event.key===' '||event.key==='Enter')){event.preventDefault();if(archiveRecordId!==null)$('archive-play').click();}});
  for(const event of ['play','pause','volumechange'])video.addEventListener(event,()=>{if(active())updateArchiveControls();});
  video.addEventListener('playing',()=>{if(active()&&!archiveSwitch){archiveAutoRecovery=null;if($('archive-playback-note').textContent.startsWith('Przygotowywanie zgodnego nagrania HEVC'))setArchiveNote('');$('archive-status').textContent='';updateArchiveControls();warmArchiveNeighbours();}});
  video.addEventListener('waiting',()=>{if(active()&&!archiveSwitch&&archiveRecordId!==null){$('archive-status').textContent='Buforowanie obrazu…';$('archive-detection-overlay').hidden=true;$('archive-object-overlay').hidden=true;}});
  video.addEventListener('seeking',()=>{if(active()){$('archive-detection-overlay').hidden=true;$('archive-object-overlay').hidden=true;}});
  video.addEventListener('error',()=>{if(active()&&!archiveSwitch&&archiveRecordId!==null){const record=records.find(item=>item.id===archiveRecordId);if(record)void diagnoseArchiveFailure(record,video.error,playbackGeneration);}});
  video.addEventListener('ended',()=>{if(active()&&!archiveSwitch)playNextRecording().catch(error=>notify(error.message,'error'));});
  video.addEventListener('timeupdate',()=>{if(!active()||archiveScrubbing||archiveSwitch)return;const record=records[recordPosition];if(!record||!video.currentSrc||!record.utc_first||record.utc_second)return;const instant=Date.parse(record.utc_first)+video.currentTime*1000;if(!Number.isFinite(instant)||cameraDay(instant)!==$('archive-date').value)return;drawDetectionOverlay(video,instant/1000);void drawArchiveObjects(video,instant);const position=eventSeconds(instant/1000);$('timeline-position').value=String(position);$('timeline-time').textContent=clock(position);warmArchiveNeighbours();});
}
bindArchiveMedia($('archive-player'));
const archiveDigitalZoom=installDigitalZoom($('archive-stage'),()=>[$('archive-player'),$('archive-detection-overlay'),$('archive-object-overlay')],()=>archiveRecordId!==null&&$('motion-region-layer').hidden);
let motionRegion=null,motionGesture=null,motionSearchEpoch=0;
function resetMotionSearch(){
  motionSearchEpoch++;motionRegion=null;motionGesture=null;$('motion-region-layer').hidden=true;$('motion-region-box').hidden=true;$('motion-search-results').replaceChildren();$('motion-search-status').textContent='';$('motion-search-run').disabled=false;$('motion-search-cancel').disabled=true;
}
function motionImageRect(){
  const video=$('archive-player'),layer=$('motion-region-layer'),r=layer.getBoundingClientRect();
  if(!video.videoWidth||!video.videoHeight||!r.width||!r.height)return null;
  const scale=Math.min(r.width/video.videoWidth,r.height/video.videoHeight),width=video.videoWidth*scale,height=video.videoHeight*scale;
  return {left:r.left+(r.width-width)/2,top:r.top+(r.height-height)/2,width,height,layerLeft:r.left,layerTop:r.top};
}
function motionPoint(event,rect){return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];}
function showMotionRegion(box,rect){const el=$('motion-region-box');el.hidden=false;el.style.left=rect.left-rect.layerLeft+box[0]*rect.width+'px';el.style.top=rect.top-rect.layerTop+box[1]*rect.height+'px';el.style.width=box[2]*rect.width+'px';el.style.height=box[3]*rect.height+'px';}
$('motion-region-select').addEventListener('click',()=>{
  const video=$('archive-player'),record=records.find(r=>r.id===archiveRecordId);
  if(!record||video.readyState<2||archiveSwitch){notify('Uruchom najpierw nagranie.');return;}
  resetMotionSearch();archiveDigitalZoom.reset();video.pause();$('motion-region-layer').hidden=false;
  $('motion-search-start').value=String(Math.max(0,Math.min(record.duration-1,Math.floor(video.currentTime*2)/2)));
  $('motion-search-duration').value=String(Math.min(120,Math.floor((record.duration-Number($('motion-search-start').value))*2)/2));
  $('motion-search-status').textContent='Przeciągnij myszą po obrazie.';
});
const motionLayer=$('motion-region-layer');
motionLayer.addEventListener('pointerdown',event=>{
  if(event.button!==0||event.isPrimary===false||motionGesture)return;const rect=motionImageRect();if(!rect)return;
  event.preventDefault();try{motionLayer.setPointerCapture(event.pointerId);}catch{return;}
  motionGesture={pointer:event.pointerId,rect,from:motionPoint(event,rect),rid:archiveRecordId};motionRegion=null;
});
function motionDrag(event){
  if(!motionGesture||event.pointerId!==motionGesture.pointer)return;
  const {rect,from}=motionGesture,to=motionPoint(event,rect),box=[Math.min(from[0],to[0]),Math.min(from[1],to[1]),Math.abs(to[0]-from[0]),Math.abs(to[1]-from[1])];
  showMotionRegion(box,rect);return box;
}
motionLayer.addEventListener('pointermove',motionDrag);
motionLayer.addEventListener('pointerup',event=>{
  const box=motionDrag(event);if(!box)return;const rid=motionGesture.rid;motionGesture=null;
  if(motionLayer.hasPointerCapture(event.pointerId))motionLayer.releasePointerCapture(event.pointerId);
  if(box[2]<.02||box[3]<.035||rid!==archiveRecordId){$('motion-region-box').hidden=true;$('motion-search-status').textContent='Wybierz większy obszar.';return;}
  motionRegion={rid,box};motionLayer.hidden=true;$('motion-search-status').textContent='Obszar zaznaczony. Ustaw zakres i kliknij Szukaj.';
});
for(const type of ['pointercancel','lostpointercapture'])motionLayer.addEventListener(type,()=>{if(motionGesture){motionGesture=null;$('motion-region-box').hidden=true;}});
$('motion-search-panel').addEventListener('toggle',()=>{if(!$('motion-search-panel').open)resetMotionSearch();});
// Split searches into small bounded requests; overlap adjacent windows so a
// transition exactly on a chunk boundary can still be observed.
function motionWindows(record,start,end){
  const result=[];
  for(let offset=start;end-offset>=1;){
    const duration=Math.min(120,end-offset);result.push({record,start:offset,duration});
    if(offset+duration>=end)break;
    if(result.length>=10000)throw new Error('Zbyt wiele odcinków. Wybierz krótszy zakres.');
    offset+=duration-0.5;
  }
  return result;
}
$('motion-search-cancel').addEventListener('click',()=>{
  motionSearchEpoch++;$('motion-search-run').disabled=false;$('motion-search-cancel').disabled=true;
  $('motion-search-status').textContent='Przerwano. Widoczne są częściowe wyniki; trwające żądanie może jeszcze zakończyć się na serwerze.';
});
$('motion-search-run').addEventListener('click',async()=>{
  const record=records.find(r=>r.id===archiveRecordId),camera=activeCamera,button=$('motion-search-run');
  if(!record||!motionRegion||motionRegion.rid!==record.id){notify('Zaznacz obszar w bieżącym nagraniu.');return;}
  const start=Number($('motion-search-start').value),duration=Number($('motion-search-duration').value),day=$('motion-search-scope').value==='day';
  if(!day&&(!Number.isFinite(start)||!Number.isFinite(duration)||start<0||duration<1||start+duration>record.duration+.000001)){notify('Nieprawidłowy zakres w bieżącym pliku.');return;}
  const selected=day?records.slice():[record],windows=[],box=motionRegion.box.slice();let skipped=0;
  try{for(const r of selected){if(!Number.isFinite(r.duration)||r.duration<1){skipped++;continue;}windows.push(...motionWindows(r,day?0:start,day?r.duration:start+duration));if(windows.length>10000)throw new Error('Zbyt wiele odcinków. Wybierz krótszy zakres.');}}catch(error){notify(error.message);return;}
  if(!windows.length){notify('Brak odcinków możliwych do analizy.');return;}
  const epoch=++motionSearchEpoch;button.disabled=true;$('motion-search-cancel').disabled=false;$('motion-search-results').replaceChildren();
  const current=()=>epoch===motionSearchEpoch&&camera===activeCamera&&!$('archive-view').hidden;
  let hits=0,failed=0;const lastEnd=new Map();
  try{
    for(let i=0;i<windows.length;i++){
      if(!current())return;const part=windows[i];
      $('motion-search-status').textContent=`Analiza ${i+1}/${windows.length} · wyniki ${hits} · błędy ${failed}`;
      let data;
      try{data=await api(`/api/recordings/${part.record.id}/motion-search`,'POST',{box,start:part.start,duration:part.duration});}
      catch(error){if(!current())return;if([401,403,429].includes(error.status))throw error;failed++;continue;}
      if(!current())return;
      for(const hit of data.hits){
        if(hit.end<=(lastEnd.get(part.record.id)??-1))continue;lastEnd.set(part.record.id,hit.end);hits++;
        // Keep the browser bounded even for a very busy day.
        if(hits>1000)continue;
        const go=element('button',`${part.record.local_time||'Nagranie '+part.record.id} · ruch ${hit.start.toFixed(1)}–${hit.end.toFixed(1)} s`);go.type='button';
        go.addEventListener('click',()=>{const index=records.findIndex(r=>r.id===part.record.id);if(index>=0&&camera===activeCamera)playRecord(index,hit.start);});$('motion-search-results').append(go);
      }
    }
    if(current())$('motion-search-status').textContent=`Zakończono: ${hits} trafień, ${failed} nieprzeanalizowanych odcinków, ${skipped} pominiętych plików.`+(hits>1000?' Pokazano pierwsze 1000 trafień.':'')+' Przerwy w nagraniach oraz krótkie ruchy mogą pozostać niewykryte.';
  }catch(error){if(current())$('motion-search-status').textContent='Analiza niepełna: '+error.message;}
  finally{if(current()){button.disabled=false;$('motion-search-cancel').disabled=true;}}
});
function cameraDay(at=Date.now()){
  const parts=new Intl.DateTimeFormat('en-GB',{timeZone:me.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(at));
  const value=(type)=>parts.find(part=>part.type===type).value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}
function seconds(text){const parts=text.split('T'),t=parts[1].split(':').map(Number);const offset=parts[0]<$('archive-date').value?-86400:parts[0]>$('archive-date').value?86400:0;return offset+t[0]*3600+t[1]*60+t[2];}
function clock(value){const n=Math.max(0,Math.min(86399,Number(value)));return [Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(x=>String(x).padStart(2,'0')).join(':');}
function clearTimelineSelection(){timelineSelection=null;$('timeline-selection').textContent='';}
function rememberTimelineSelection(position){
  timelineSelection={date:$('archive-date').value,position:Number(position),fold:$('timeline-fold').value};
  $('timeline-selection').textContent='Zaznaczono: '+timelineSelection.date+' '+clock(timelineSelection.position);
}
async function timelineMatch(value,allowManualAmbiguous=false){
  const date=$('archive-date').value,fold=$('timeline-fold').value;
  const result=await api('/api/time/resolve','POST',{local:date+'T'+clock(value),fold:fold===''?null:Number(fold)});
  const instant=Date.parse(result.utc)/1000;
  if(!Number.isFinite(instant))throw new Error('Nieprawidłowy czas osi.');
  const index=records.findIndex(record=>containsInstant(record,instant));
  if(index<0)return null;
  if(records[index].utc_second){if(allowManualAmbiguous)return {index,offset:0,ambiguous:true};throw new Error('Starsze nagranie ma niejednoznaczny czas i nie da się ustalić jego wystąpienia.');}
  return {index,offset:instant-Date.parse(records[index].utc_first)/1000};
}
function timelineSpans(record){
  const parts=$('archive-date').value.split('-').map(Number),day=Date.UTC(parts[0],parts[1]-1,parts[2])/1000;
  if(timelineFormatterZone!==me.timezone){timelineFormatterZone=me.timezone;timelineFormatter=new Intl.DateTimeFormat('en-GB',{timeZone:me.timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});}
  function offset(instant){
    const fields=timelineFormatter.formatToParts(new Date(instant*1000));const get=name=>Number(fields.find(field=>field.type===name).value);
    return Date.UTC(get('year'),get('month')-1,get('day'),get('hour'),get('minute'),get('second'))/1000-Math.floor(instant);
  }
  const spans=[];
  for(const timestamp of [record.utc_first,record.utc_second]){
    if(!timestamp)continue;
    let start=Date.parse(timestamp)/1000,end=start+Number(record.duration);
    if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)continue;
    while(start<end){
      const current=offset(start),last=offset(Math.max(start,end-0.001));let boundary=end;
      if(current!==last){
        let low=start,high=end;
        while(high-low>0.001){const mid=(low+high)/2;if(offset(mid)===current)low=mid;else high=mid;}
        boundary=high;
      }
      const left=Math.max(0,start+current-day),right=Math.min(86400,boundary+current-day);
      if(right>left)spans.push([left,right]);
      start=boundary;
    }
  }
  return spans;
}
function renderTimeline(){
  const track=$('timeline-track');track.replaceChildren();
  const spans=records.flatMap(timelineSpans).sort((a,b)=>a[0]-b[0]);const merged=[];for(const span of spans){const last=merged[merged.length-1];if(last&&span[0]<=last[1]+0.01)last[1]=Math.max(last[1],span[1]);else merged.push(span.slice());}
  for(const [start,end] of merged){const block=element('span',null,'timeline-segment');block.style.left=(start/86400*100)+'%';block.style.width=((end-start)/86400*100)+'%';track.append(block);}
  const selected=$('event-filter').value;
  for(const event of eventMarkers.filter(item=>selected==='all'||item.type===selected)){const position=eventSeconds(event.timestamp);const marker=element('span',null,'timeline-event');marker.style.left=(position/86400*100)+'%';marker.title=event.type;marker.dataset.kind=event.type;track.append(marker);}
  if(records.some(r=>r.utc_second))notify('Uwaga: część nagrań ma niejednoznaczną godzinę po zmianie czasu.');
}
function eventSeconds(timestamp){const pieces=new Intl.DateTimeFormat('en-GB',{timeZone:me.timezone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(timestamp*1000));const get=(type)=>Number(pieces.find(p=>p.type===type).value);return get('hour')*3600+get('minute')*60+get('second');}
const eventLabels={motion:'Ruch',person:'Człowiek',vehicle:'Pojazd',animal:'Zwierzę',face:'Twarz',package:'Paczka',visitor:'Dzwonek',tamper:'Sabotaż'};
function eventLabel(kind){return eventLabels[kind]||({line_cross:'Przekroczenie linii',intrusion:'Wtargnięcie',abandoned:'Pozostawiony przedmiot',plate:'Tablica',object:'Obiekt'})[kind]||kind;}
function seekToEvent(event){
  const position=eventSeconds(event.timestamp),fold=event.fold===0||event.fold===1?String(event.fold):'';
  timelineRequestGeneration++;clearTimeout(previewTimer);$('timeline-preview').hidden=true;
  $('timeline-fold').value=fold;$('timeline-position').value=String(position);$('timeline-time').textContent=clock(position);rememberTimelineSelection(position);
  const index=records.findIndex(r=>!r.utc_second&&r.utc_first&&Date.parse(r.utc_first)/1000<=event.timestamp&&event.timestamp<Date.parse(r.utc_first)/1000+r.duration);
  if(index>=0){if(event.box&&!eventMarkers.some(e=>e.id===event.id))eventMarkers=[...eventMarkers,event];playRecord(index,event.timestamp-Date.parse(records[index].utc_first)/1000);}else notify('Brak jednoznacznego nagrania dla tego zdarzenia.');
}
function renderEvents(){
  const area=$('events');area.replaceChildren();const selected=$('event-filter').value;
  for(const event of eventMarkers.filter(e=>selected==='all'||e.type===selected)){
    const fold=event.fold===0||event.fold===1?String(event.fold):'';
    const suffix=fold===''?'':` (${Number(fold)+1}. wystąpienie)`;
    const duration=event.ended_at!=null?` · ${Math.max(0,event.ended_at-event.timestamp)} s`:'';
    const button=element('button',clock(eventSeconds(event.timestamp))+suffix+' - '+eventLabel(event.type)+duration+(event.zone?' · '+event.zone:''));
    button.addEventListener('click',()=>seekToEvent(event));area.append(button);
  }
}
// A box belongs to a single observation, never the whole event duration.
// Render only close to that observation and align to the actual contained video.
let boxEventSource=null,boxEvents=[];
function drawDetectionOverlay(video,instant){
  const overlay=$('archive-detection-overlay');if(!overlay)return;overlay.hidden=true;overlay.replaceChildren();
  if(!video.videoWidth||!video.videoHeight||!video.clientWidth||!video.clientHeight||!Number.isFinite(instant))return;
  if(boxEventSource!==eventMarkers){boxEventSource=eventMarkers;boxEvents=eventMarkers.filter(e=>Array.isArray(e.box)&&e.box.length===4);}
  const observed=boxEvents.filter(e=>Math.abs(e.timestamp-instant)<=0.5);
  if(!observed.length)return;
  const scale=Math.min(video.clientWidth/video.videoWidth,video.clientHeight/video.videoHeight),w=video.videoWidth*scale,h=video.videoHeight*scale;
  const x=(video.clientWidth-w)/2,y=(video.clientHeight-h)/2;
  for(const e of observed){const [bx,by,bw,bh]=e.box;if(![bx,by,bw,bh].every(Number.isFinite)||bx<0||by<0||bw<=0||bh<=0||bx+bw>1.000001||by+bh>1.000001)continue;
    const box=element('span',eventLabel(e.type),'detection-box');box.dataset.kind=e.type;box.style.left=(x+bx*w)+'px';box.style.top=(y+by*h)+'px';box.style.width=(bw*w)+'px';box.style.height=(bh*h)+'px';overlay.append(box);}
  overlay.hidden=!overlay.children.length;
}
// Use only observed camera coordinates, never generated boxes. Keep trails session-local.
function paintNativeObjects(overlay,video,frame,tracks=null){
  if(!overlay)return;overlay.hidden=true;overlay.replaceChildren();
  if(!frame||!video?.videoWidth||!video.videoHeight||!video.clientWidth||!video.clientHeight)return;
  const scale=Math.min(video.clientWidth/video.videoWidth,video.clientHeight/video.videoHeight),w=video.videoWidth*scale,h=video.videoHeight*scale,x=(video.clientWidth-w)/2,y=(video.clientHeight-h)/2;
  if(tracks&&tracks.session!==frame.session){tracks.session=frame.session;tracks.paths.clear();}
  const seen=new Set();
  for(const obj of frame.objects||[]){const b=obj.box;if(!Array.isArray(b)||b.length!==4||!b.every(Number.isFinite))continue;const [bx,by,bw,bh]=b;if(bx<0||by<0||bw<=0||bh<=0||bx+bw>1.000001||by+bh>1.000001)continue;
    const box=element('span',(obj.label==='foreground_change'?'Zmiana obrazu':obj.label||eventLabel(obj.kind))+' #'+obj.id+(obj.plate_number?' · '+obj.plate_number:'')+(obj.face_match?' · możliwe dopasowanie: '+obj.face_match:''),'detection-box');box.dataset.kind=obj.kind;Object.assign(box.style,{left:(x+bx*w)+'px',top:(y+by*h)+'px',width:bw*w+'px',height:bh*h+'px'});overlay.append(box);seen.add(obj.id);
    if(tracks){let path=tracks.paths.get(obj.id)||[];if(path.length&&frame.stamp_ms-path[path.length-1].t>3000)path=[];if(!path.length||path[path.length-1].t<frame.stamp_ms)path.push({t:frame.stamp_ms,x:bx+bw/2,y:by+bh/2});path=path.slice(-12);tracks.paths.set(obj.id,path);for(const point of path){const dot=element('span',null,'native-track-dot');dot.style.left=(x+point.x*w)+'px';dot.style.top=(y+point.y*h)+'px';overlay.append(dot);}}
  }
  if(tracks)for(const key of tracks.paths.keys())if(!seen.has(key))tracks.paths.delete(key);
  overlay.hidden=!overlay.children.length;
}
function combineObservations(native,motion){
  if(!native&&!motion)return null;
  return {stamp_ms:Math.max(native?.stamp_ms||0,motion?.stamp_ms||0),session:(native?.session||'')+'|'+(motion?.session||''),objects:[...(native?.objects||[]),...(motion?.objects||[])]};
}
let nativePollBusy=false;const nativeTracks=new Map();
async function pollNativeObjects(){
  if(nativePollBusy||!me||document.hidden||$('camera-view').hidden)return;
  nativePollBusy=true;const owner=me.id;let frames={},motion={};try{const result=await api('/api/metadata/live');frames=result.cameras||{};motion=result.motion||{};}catch(error){}finally{nativePollBusy=false;}
  if(!me||me.id!==owner||$('camera-view').hidden){nativeTracks.clear();return;}
  const visible=new Set();for(const card of $('cameras').querySelectorAll('.camera')){const id=card.dataset.cameraId;visible.add(id);const player=card.querySelector('video-stream');if(!player?.sunhillOverlay)continue;const frame=combineObservations(frames[id],motion[id]);if(!frame)nativeTracks.delete(id);if(!nativeTracks.has(id))nativeTracks.set(id,{session:null,paths:new Map()});paintNativeObjects(player.sunhillOverlay,player.video,frame,nativeTracks.get(id));}
  for(const id of nativeTracks.keys())if(!visible.has(id))nativeTracks.delete(id);
}
setInterval(pollNativeObjects,1000);
let nativeArchiveCache={camera:null,at:0,frames:[]},nativeArchivePending=false;
async function drawArchiveObjects(video,instant){
  const overlay=$('archive-object-overlay');if(!overlay||!activeCamera)return;
  const cid=activeCamera.id,gen=playbackGeneration;
  if(nativeArchiveCache.camera!==cid||Math.abs(nativeArchiveCache.at-instant)>4000){overlay.hidden=true;if(nativeArchivePending)return;nativeArchivePending=true;try{const result=await api(`/api/cameras/${cid}/metadata-history?at_ms=${Math.round(instant)}`);if(activeCamera?.id!==cid||gen!==playbackGeneration||$('archive-view').hidden)return;nativeArchiveCache={camera:cid,at:instant,frames:result.frames||[],motion:result.motion_frames||[]};}catch(error){return;}finally{nativeArchivePending=false;}}
  if(!me||activeCamera?.id!==cid||gen!==playbackGeneration||video!==$('archive-player')||$('archive-view').hidden||video.seeking||video.readyState<2)return;
  const record=records[recordPosition],now=record?Date.parse(record.utc_first)+video.currentTime*1000:NaN;if(!Number.isFinite(now))return;
  const frame=nativeArchiveCache.frames.filter(f=>Math.abs(f.stamp_ms-now)<=600).sort((a,b)=>Math.abs(a.stamp_ms-now)-Math.abs(b.stamp_ms-now))[0];const motion=(nativeArchiveCache.motion||[]).filter(f=>Math.abs(f.stamp_ms-now)<=600).sort((a,b)=>Math.abs(a.stamp_ms-now)-Math.abs(b.stamp_ms-now))[0];paintNativeObjects(overlay,video,combineObservations(frame,motion));
}
function firmwareSettingsPanel(camera){
  const panel=element('details'),status=element('p'),endpoint=`/api/cameras/${camera.id}/firmware`;
  panel.append(element('summary','Aktualizacja firmware kamery'),element('p','Używaj wyłącznie pliku i SHA-256 z zaufanego źródła producenta. Porównaj model i rewizję. Nie odłączaj zasilania. Przy utracie odpowiedzi nie ponawiaj automatycznie.'));
  const read=element('button','Odczytaj tożsamość kamery');read.type='button';
  const fields=['Manufacturer','Model','HardwareId'].map(k=>[k,labeledInput(k,'text','')]);
  const hash=labeledInput('SHA-256 producenta','text',''),targetVersion=labeledInput('Oczekiwana wersja firmware (dokładna)','text','');const file=element('input');file.type='file';const send=element('button','Wyślij firmware');send.type='button';send.disabled=true;
  let verified=null,busy=false;
  read.onclick=async()=>{if(busy)return;busy=true;send.disabled=true;verified=null;try{const data=await api(endpoint);verified=data;for(const [k,f] of fields)f.input.value=data[k]||'';status.textContent='Aktualna wersja: '+data.FirmwareVersion+'; porównaj dane z dokumentacją producenta.';send.disabled=false;}catch(error){status.textContent=error.message;}finally{busy=false;}};
  send.onclick=async()=>{if(busy||!verified)return;const selected=file.files?.[0],sha=hash.input.value.trim(),version=targetVersion.input.value.trim();const expected=Object.fromEntries(fields.map(([k,f])=>[k,f.input.value.trim()]));
    if(!selected||selected.size<1||selected.size>128*1024*1024||!version||version.length>256||version===verified.FirmwareVersion||!(/^[0-9a-fA-F]{64}$/).test(sha)||fields.some(([k])=>expected[k]!==verified[k])){status.textContent='Sprawdź plik, SHA-256, tożsamość sprzętu i nową wersję firmware.';return;}
    if(!window.confirm('Potwierdzasz zgodność firmware z modelem i rewizją kamery '+camera.name+'? Aktualizacja może przerwać nagrywanie.'))return;
    busy=true;send.disabled=read.disabled=true;status.textContent='Przesyłanie i weryfikacja. Nie odłączaj kamery.';
    try{const params=new URLSearchParams({...expected,sha256:sha,confirm:'yes'});const response=await fetch(endpoint+'?'+params,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':cookie('sunhill_csrf')},body:selected});const result=await response.json();if(!response.ok||!result.uploaded)throw new Error(result.error||'Brak potwierdzenia');status.textContent='Kamera potwierdziła upload. Oczekiwanie na wersję '+version+'; nie odłączaj zasilania.';
      const deadline=Date.now()+180000;let confirmed=false;
      while(panel.isConnected&&Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,5000));if(!panel.isConnected)break;
        let found;try{found=await api(endpoint);}catch(error){continue;}
        if(fields.some(([key])=>found[key]!==expected[key]))throw new Error('Tożsamość kamery zmieniła się po uploadzie.');
        if(found.FirmwareVersion===version){confirmed=true;status.textContent='Potwierdzona nowa wersja: '+version;break;}
        status.textContent='Odczytana wersja: '+found.FirmwareVersion+'; oczekiwana: '+version+'.';
      }
      if(!confirmed&&panel.isConnected)status.textContent='Upload potwierdzony, lecz nowa wersja niepotwierdzona. Nie ponawiaj automatycznie.';}catch(error){status.textContent=error.message+' Nie ponawiaj bez sprawdzenia kamery.';}finally{busy=false;read.disabled=false;verified=null;}
  };
  panel.append(read,...fields.map(([,f])=>f.label),hash.label,targetVersion.label,file,send,status);return panel;
}
function metadataSettingsPanel(camera){
  const panel=element('details');panel.append(element('summary','Metadane ONVIF, lokalna analiza i reguły przestrzenne'));const status=element('p'),fields=element('div');panel.append(status,fields);let loaded=false,busy=false;
  const endpoint=`/api/cameras/${camera.id}/metadata-settings`;
  async function load(){if(busy)return;busy=true;try{const cfg=await api(endpoint);fields.replaceChildren();const enabled=element('input');enabled.type='checkbox';enabled.checked=cfg.enabled;const label=element('label','Analiza obiektów (ONVIF i lokalna)','inline');label.prepend(enabled);const sourceMode=element('select');sourceMode.add(new Option('Automatycznie: ONVIF, następnie lokalna analiza','auto'));sourceMode.add(new Option('Lokalna analiza (także dla kamer z ONVIF)','local'));sourceMode.value=cfg.source_mode||'auto';const sourceLabel=element('label','Źródło analizy');sourceLabel.append(sourceMode);fields.append(label,sourceLabel,element('p','Metadane są przechowywane wraz z dostępnymi nagraniami.'),element('p','Lokalnie wykrywa osoby, twarze i tablice. Opcjonalny lokalny model COCO rozszerza klasy obiektów. Profile twarzy wymagają osobnego, świadomego zapisania przez administratora oraz modeli YuNet/SFace. Dopasowania są orientacyjne. Zmiana obrazu jest heurystyką, nie klasyfikacją AI. Reguła pozostawionych przedmiotów wymaga obserwacji obiektu lub paczki i osoby.'));
    let rules=cfg.rules||[],points=[],editing=null;const list=element('div'),stage=element('div',null,'zone-editor-stage'),image=element('img'),drawing=document.createElementNS('http://www.w3.org/2000/svg','svg');image.alt='Kadr referencyjny';image.hidden=true;drawing.setAttribute('viewBox','0 0 1000 1000');drawing.setAttribute('preserveAspectRatio','none');stage.append(image,drawing);
    const name=labeledInput('Nazwa reguły','text',''),type=element('select'),kind=element('select'),direction=element('select'),dwell=labeledInput('Czas przebywania / bezruchu (s)','number',0);dwell.input.min='0';dwell.input.max='600';for(const k of ['line_cross','intrusion','abandoned'])type.add(new Option(eventLabel(k),k));for(const k of ['all','person','vehicle','animal','face','plate','package','object'])kind.add(new Option(k==='all'?'Wszystkie':eventLabel(k),k));for(const [k,title] of [['both','Oba kierunki'],['positive','Ujemna → dodatnia strona'],['negative','Dodatnia → ujemna strona']])direction.add(new Option(title,k));
    function labelFor(title,input){const l=element('label',title);l.append(input);return l;}
    function paint(){drawing.replaceChildren();if(points.length){const poly=document.createElementNS(drawing.namespaceURI,type.value==='line_cross'||points.length<3?'polyline':'polygon');poly.setAttribute('points',points.map(p=>p.map(n=>Math.round(n*1000)).join(',')).join(' '));poly.setAttribute('fill',type.value==='line_cross'?'none':'#38a6ff33');poly.setAttribute('stroke','#38a6ff');poly.setAttribute('stroke-width','4');drawing.append(poly);for(const p of points){const dot=document.createElementNS(drawing.namespaceURI,'circle');dot.setAttribute('cx',p[0]*1000);dot.setAttribute('cy',p[1]*1000);dot.setAttribute('r','8');dot.setAttribute('fill','#fff');drawing.append(dot);}}}
    function refreshList(){list.replaceChildren();for(const rule of rules){const row=element('div',null,'inline');const edit=element('button',rule.name+' - '+eventLabel(rule.type));edit.type='button';edit.onclick=()=>{editing=rule.id;name.input.value=rule.name;type.value=rule.type;kind.value=rule.kind;direction.value=rule.direction;dwell.input.value=rule.dwell;points=rule.points.map(p=>p.slice());paint();};const remove=element('button','Usuń');remove.type='button';remove.onclick=()=>{rules=rules.filter(r=>r.id!==rule.id);if(editing===rule.id){editing=null;points=[];paint();}refreshList();};row.append(edit,remove);list.append(row);}}
    stage.addEventListener('click',ev=>{const r=stage.getBoundingClientRect();if(!r.width||!r.height||points.length>=(type.value==='line_cross'?2:32))return;points.push([Math.max(0,Math.min(1,(ev.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(ev.clientY-r.top)/r.height))].map(v=>Math.round(v*10000)/10000));paint();});
    type.onchange=()=>{points=[];if(type.value==='abandoned'){kind.value='package';dwell.input.value=30;}paint();};const clear=element('button','Wyczyść punkty');clear.type='button';clear.onclick=()=>{points=[];paint();};const undo=element('button','Cofnij punkt');undo.type='button';undo.onclick=()=>{points.pop();paint();};
    const frame=element('button','Pobierz kadr z dzisiejszego archiwum');frame.type='button';frame.onclick=async()=>{frame.disabled=true;try{const data=await api(`/api/cameras/${camera.id}/recordings?date=${encodeURIComponent(cameraDay())}`);const last=data.recordings?.at(-1);if(!last)throw new Error('Brak nagrań dzisiaj.');image.onload=()=>{stage.style.aspectRatio=`${image.naturalWidth}/${image.naturalHeight}`;image.hidden=false;};image.onerror=()=>{image.hidden=true;status.textContent='Nie udało się odczytać kadru.';};image.src=`/api/recordings/${last.id}/frame?offset=0`;}catch(error){status.textContent=error.message;}finally{frame.disabled=false;}};
    const add=element('button','Dodaj / zmień regułę');add.type='button';add.onclick=()=>{if(!name.input.value.trim()||(type.value==='line_cross'?points.length!==2:points.length<3)||(!editing&&rules.length>=16)){status.textContent='Podaj nazwę i poprawną geometrię (2 punkty linii lub min. 3 punkty strefy).';return;}const id=editing||('r'+Array.from(crypto.getRandomValues(new Uint8Array(8)),x=>x.toString(16).padStart(2,'0')).join(''));rules=rules.filter(r=>r.id!==id);rules.push({id,name:name.input.value.trim(),type:type.value,points:points.slice(),kind:kind.value,direction:direction.value,dwell:Number(dwell.input.value)});editing=null;points=[];name.input.value='';paint();refreshList();status.textContent='Reguła w formularzu. Zapisz wszystkie ustawienia.';};
    const save=element('button','Zapisz wszystkie ustawienia');save.type='button';save.onclick=async()=>{save.disabled=true;try{const result=await api(endpoint,'PATCH',{enabled:enabled.checked,rules,source_mode:sourceMode.value});status.textContent='Zapisano. Stan: '+(result.status?.state||'oczekiwanie');}catch(error){status.textContent=error.message;}finally{save.disabled=false;}};
    fields.append(list,name.label,labelFor('Typ',type),labelFor('Obiekt',kind),labelFor('Kierunek linii',direction),dwell.label,frame,element('p','Klikaj w kadrze, aby dodać kolejne wierzchołki; bez kadru dostępna jest siatka referencyjna.'),stage,undo,clear,add,save);refreshList();status.textContent='Stan: '+(cfg.status?.state||(cfg.enabled?'oczekiwanie':'wyłączone'));loaded=true;
  }catch(error){status.textContent=error.message;}finally{busy=false;}}
  panel.addEventListener('toggle',()=>{if(panel.open&&!loaded)void load();});return panel;
}
$('database-refresh').addEventListener('click',async()=>{try{const d=await api('/api/settings/database');$('database-status').textContent=`SQLite ${d.journal.toUpperCase()} · ${(d.bytes/1e6).toFixed(1)} MB · WAL ${(d.wal_bytes/1e6).toFixed(1)} MB · Nagrania ${d.recordings} · Zdarzenia ${d.events} · Wolne strony ${d.free_pages}/${d.page_count}`;}catch(error){$('database-status').textContent=error.message;}});
for(const [id,operation] of [['database-check','quick_check'],['database-optimize','optimize']])$(id).addEventListener('click',async()=>{const status=$('database-maintenance-status');$('database-check').disabled=$('database-optimize').disabled=true;status.textContent='Trwa operacja bazy…';try{const result=await api('/api/settings/database/maintenance','POST',{operation});status.textContent=result.ok?(operation==='quick_check'?'Kontrola integralności: OK.':'Optymalizacja statystyk zakończona.'):'Kontrola integralności wykazała nieprawidłowości.';}catch(error){status.textContent=error.message;}finally{$('database-check').disabled=$('database-optimize').disabled=false;}});
function eventSettingsPanel(camera){
  const panel=element('details');panel.append(element('summary','Detekcja i zdarzenia AI'));
  const status=element('p','Odczyt ustawień…'),fields=element('div');panel.append(status,fields);let busy=false;
  const endpoint=`/api/cameras/${camera.id}/event-settings`;
  async function load(){if(busy)return;busy=true;try{const cfg=await api(endpoint);fields.replaceChildren();const enabled=element('input');enabled.type='checkbox';enabled.checked=cfg.enabled;const label=element('label','Rejestruj zdarzenia kamery','inline');label.prepend(enabled);fields.append(label);
    const types=[];for(const [value,title] of Object.entries(eventLabels)){const check=element('input');check.type='checkbox';check.checked=true;check.disabled=true;const l=element('label',title,'inline');l.prepend(check);fields.append(l);types.push([value,check]);}
    fields.append(element('p','Wszystkie rodzaje zdarzeń są rejestrowane. Metadane pozostają wraz z dostępnymi nagraniami.'),element('p','Zasłonięcie: także lokalna heurystyka jednolitego kadru; ciemność lub mgła mogą wywołać alarm.'));
    const save=element('button','Zapisz ustawienia zdarzeń');save.type='button';save.addEventListener('click',async()=>{if(busy)return;busy=true;save.disabled=true;try{await api(endpoint,'PATCH',{enabled:enabled.checked,types:types.map(x=>x[0])});status.textContent='Zapisano. Zmiany zostaną zastosowane w tle.';}catch(error){status.textContent=error.message;}finally{busy=false;save.disabled=false;}});const refresh=element('button','Odśwież status');refresh.type='button';refresh.addEventListener('click',load);fields.append(save,refresh);
    const st=cfg.status;status.textContent=!cfg.enabled?'Rejestracja wyłączona.':st?`${st.state==='active'&&Date.now()/1000-st.checked_at<90?'Połączono':'Brak aktualnego połączenia'} · ${st.transport||'wykrywanie'} · ${new Date(st.checked_at*1000).toLocaleString()}`:'Oczekiwanie na wykrycie obsługi.';
  }catch(error){status.textContent=error.message;}finally{busy=false;}}
  panel.addEventListener('toggle',()=>{if(panel.open)void load();});return panel;
}
$('event-filter').addEventListener('change',()=>{renderEvents();renderTimeline();});
$('event-token-create').addEventListener('click',async()=>{if(!window.confirm('Utworzenie tokenu unieważni poprzedni. Kontynuować?'))return;try{const result=await api('/api/integrations/events/token','POST',{});$('event-token').value=result.token;notify('Token pokazany tylko teraz. Skopiuj go w bezpieczne miejsce.');}catch(error){notify(error.message,'error');}});
$('event-token-revoke').addEventListener('click',async()=>{if(!window.confirm('Unieważnić token zdarzeń?'))return;try{await api('/api/integrations/events/token','DELETE');$('event-token').value='';notify('Token unieważniony.');}catch(error){notify(error.message,'error');}});
let previewTimer=null;
$('timeline-position').addEventListener('input',(event)=>{
  const position=Number(event.target.value),camera=activeCamera,generation=archiveLoadGeneration,request=++timelineRequestGeneration;
  archiveScrubbing=true;rememberTimelineSelection(position);
  $('timeline-time').textContent=clock(position);clearTimeout(previewTimer);$('timeline-preview').hidden=true;
  previewTimer=setTimeout(async()=>{
    try{
      const match=await timelineMatch(position);
      if(request!==timelineRequestGeneration||camera!==activeCamera||generation!==archiveLoadGeneration||!match)return;
      const image=$('timeline-preview');image.src=`/api/recordings/${records[match.index].id}/frame?offset=${match.offset}`;image.hidden=false;
    }catch(error){/* A preview is optional; the selection reports errors. */}
  },300);
});
$('timeline-position').addEventListener('change',async(event)=>{
  const position=Number(event.target.value),camera=activeCamera,generation=archiveLoadGeneration,request=++timelineRequestGeneration;
  archiveScrubbing=true;rememberTimelineSelection(position);
  clearTimeout(previewTimer);$('timeline-preview').hidden=true;
  try{
    const match=await timelineMatch(position,true);
    if(request!==timelineRequestGeneration||camera!==activeCamera||generation!==archiveLoadGeneration)return;
    if(match?.ambiguous){if(window.confirm('Nie można ustalić dokładnej godziny tego starszego nagrania. Odtworzyć oryginalny plik od początku?'))playRecord(match.index,0);}
    else if(match)playRecord(match.index,match.offset);else notify('Brak nagrania w wybranym czasie.');
  }catch(error){if(request===timelineRequestGeneration&&camera===activeCamera&&generation===archiveLoadGeneration)notify(error.message,'error');}
  finally{if(request===timelineRequestGeneration)archiveScrubbing=false;}
});
$('timeline-position').addEventListener('pointercancel',()=>{archiveScrubbing=false;});
$('timeline-position').addEventListener('blur',()=>{archiveScrubbing=false;});
$('timeline-track').addEventListener('click',event=>{const rect=$('timeline-track').getBoundingClientRect();if(!rect.width)return;const position=Math.max(0,Math.min(86399,Math.round((event.clientX-rect.left)/rect.width*86400)));const range=$('timeline-position');range.value=String(position);$('timeline-time').textContent=clock(position);range.dispatchEvent(new Event('change'));});
$('timeline-fold').addEventListener('change',()=>{timelineRequestGeneration++;clearTimeout(previewTimer);$('timeline-preview').hidden=true;if(timelineSelection)timelineSelection.fold=$('timeline-fold').value;});
function setExportBoundary(which){
  const selection=timelineSelection||{date:$('archive-date').value,position:Number($('timeline-position').value),fold:$('timeline-fold').value};
  $('export-form').elements[which].value=selection.date+'T'+clock(selection.position);
  $('export-form').elements[which+'_fold'].value=selection.fold;
  clearTimelineSelection();
}
$('set-export-start').addEventListener('click',()=>setExportBoundary('start'));
$('set-export-end').addEventListener('click',()=>setExportBoundary('end'));
$('save-frame').addEventListener('click',()=>{
  const record=records[recordPosition],player=$('archive-player');
  if(!activeCamera||!activeCamera.may_download||!record)return;
  try{
    const current=new URL(player.currentSrc,location.origin);
    if(current.origin!==location.origin||current.pathname!==`/api/recordings/${record.id}/file`||player.readyState<2||!Number.isFinite(player.currentTime))throw new Error();
  }catch{notify('Najpierw uruchom nagranie i poczekaj na obraz.');return;}
  const a=element('a');a.href=`/api/recordings/${record.id}/frame?offset=${Math.max(0,player.currentTime)}&download=1`;a.click();
});
function labeledInput(labelText,type,value=''){
  const label=element('label',labelText);const input=element('input');input.type=type;input.value=value;label.append(input);return {label,input};
}
function formatStorageBytes(value){
  if(!Number.isFinite(value)||value<0)return '—';
  if(value>=1e12)return (value/1e12).toFixed(2)+' TB';
  if(value>=1e9)return (value/1e9).toFixed(1)+' GB';
  if(value>=1e6)return (value/1e6).toFixed(1)+' MB';
  return value+' B';
}
function formatCameraProfile(profile,state,stamp){
  if(!profile)return ({pending:'Oczekuje na pomiar',failed:'Pomiar nieudany - ponowię automatycznie',not_configured:'Nie skonfigurowano'})[state]||'Brak aktualnego pomiaru';
  const label=state==='last_known'?' · ostatni udany pomiar'+(stamp?' ('+new Date(stamp*1000).toLocaleString('pl-PL')+')':''):'';
  return `${profile.width} × ${profile.height} · ${profile.fps==null?'—':Number(profile.fps).toFixed(2).replace(/\.?0+$/,'')} FPS · ${profile.codec.toUpperCase()} · ${profile.video_bitrate==null?'bitrate nieznany':(profile.video_bitrate/1e6).toFixed(2)+' Mb/s'}${label}`;
}
async function refreshCameraStatistics(generation){
  const status=$('camera-statistics-status');if(!status||!me||me.role!=='admin')return;
  status.textContent='Odczyt statystyk…';
  try{
    const report=await api('/api/admin/camera-statistics');
    if(generation!==adminRefreshGeneration||!me||me.role!=='admin')return;
    for(const card of $('manage-cameras').querySelectorAll('.camera-config')){
      const data=report.cameras[card.dataset.cameraId],target=card.querySelector('.camera-statistics');
      if(!target||!data)continue;
      target.replaceChildren();
      const health={recording:'Nagrywa',stale:'Brak nowych nagrań',offline:'Offline',waiting:'Oczekiwanie',disabled:'Wyłączone',clock_skew:'Błąd czasu',unknown:'Nieznany'};
      const items=[['Główny',formatCameraProfile(data.main,data.main_state,data.main_checked_at)],['Pomocniczy',data.sub_configured?formatCameraProfile(data.sub,data.sub_state,data.sub_checked_at):'Nie skonfigurowano'],['Zapis 24 h',formatStorageBytes(data.bytes_24h)+' · '+data.files_24h+' plików'+(data.full_day?'':' (niepełna doba)')],['Archiwum',`${data.files} plików · ${formatStorageBytes(data.bytes)}`],['Nagrywanie',health[data.health]||'Nieznany'],['Pokrycie ostatnich 24 h',(data.covered_seconds/3600).toFixed(1)+' h'],['Przerwy > 30 s',`${data.gaps} między segmentami · ${(data.gap_seconds/60).toFixed(1)} min`]];
      for(const [name,value] of items){const item=element('span',null,'camera-statistics-item');item.append(element('span',name),element('strong',value));target.append(item);}
      if(data.ambiguous)target.append(element('span',`${data.ambiguous} nagrań z niejednoznacznym czasem pominięto w analizie przerw.`,'camera-statistics-note'));
      if(data.last_file_at)target.append(element('span','Ostatnie nagranie do: '+new Date(data.last_file_at*1000).toLocaleString('pl-PL'),'camera-statistics-note'));
      if(data.checked_at)target.append(element('span','Pomiar strumieni: '+new Date(data.checked_at*1000).toLocaleString('pl-PL')+(data.stream_fresh?'':' (nie wszystkie profile aktualne)'),'camera-statistics-note'));
    }
    const disk=report.disk;$('overview-disk-free').textContent=formatStorageBytes(disk.free_bytes);
    $('overview-disk-daily').textContent=formatStorageBytes(disk.active_bytes_24h);
    $('overview-retention').textContent=disk.forecast_ready?disk.retention_days.toFixed(1)+' dni':'Brak pełnej próbki';
    $('overview-reserve-days').textContent=disk.forecast_ready?disk.days_until_reserve.toFixed(1)+' dni':'—';
    status.textContent='Statystyki z indeksu nagrań i okresowych prób strumieni. Bitrate to pomiar krótkiej próbki wideo.';
  }catch(error){if(generation===adminRefreshGeneration)status.textContent='Nie udało się odczytać statystyk: '+error.message;}
}
async function refreshAdmin() {
  const generation=++adminRefreshGeneration;
  const {users,cameras:adminCameras}=await api('/api/admin/inventory');
  if(generation!==adminRefreshGeneration)return;
  const [ha,storage,monitoring,audit,recording]=await Promise.all([api('/api/settings/home-assistant'),api('/api/settings/storage'),api('/api/settings/monitoring'),api('/api/audit'),api('/api/settings/recording')]);
  if(generation!==adminRefreshGeneration)return;
  const usersBox=$('users');usersBox.replaceChildren();
  for(const user of users){
    const details=element('details');details.append(element('summary',user.username+' - '+user.role+(user.enabled?'':' - wyłączony')));
    const role=element('select');role.add(new Option('Użytkownik','viewer'));role.add(new Option('Administrator','admin'));role.value=user.role;const roleLabel=element('label','Rola');roleLabel.append(role);details.append(roleLabel);const roleSave=element('button','Zmień rolę');roleSave.type='button';roleSave.addEventListener('click',async()=>{try{await api(`/api/users/${user.id}`,'PATCH',{role:role.value});await refreshAdmin();}catch(error){notify(error.message,'error');}});details.append(roleSave);
    const toggle=element('button',user.enabled?'Wyłącz konto':'Włącz konto');toggle.type='button';toggle.addEventListener('click',async()=>{try{await api(`/api/users/${user.id}`,'PATCH',{enabled:!user.enabled});await refreshAdmin();}catch(error){notify(error.message,'error');}});details.append(toggle);
    const change=labeledInput('Nowe hasło (min. 6 znaków)','password');change.input.minLength=6;change.input.maxLength=1024;change.input.autocomplete='new-password';details.append(change.label);const save=element('button','Zmień hasło');save.type='button';save.addEventListener('click',async()=>{try{await api(`/api/users/${user.id}`,'PATCH',{password:change.input.value});change.input.value='';notify('Hasło zmienione.');}catch(error){notify(error.message,'error');}});details.append(save);
    if(user.role!=='admin'){
      const grants=user.permissions;const list=element('div');const controls=[];
      for(const camera of adminCameras){const current=grants.find(g=>g.id===camera.id);const label=element('label',camera.name,'inline');const access=element('input');access.type='checkbox';access.checked=Boolean(current);label.prepend(access);const downloadLabel=element('label','Pobieranie','inline');const download=element('input');download.type='checkbox';download.checked=Boolean(current&&current.download);downloadLabel.prepend(download);list.append(label,downloadLabel);controls.push({id:camera.id,access,download});}
      const apply=element('button','Zapisz uprawnienia');apply.type='button';apply.addEventListener('click',async()=>{try{await api(`/api/users/${user.id}/permissions`,'POST',{cameras:controls.filter(c=>c.access.checked).map(c=>({id:c.id,download:c.download.checked}))});notify('Uprawnienia zapisane.');}catch(error){notify(error.message,'error');}});details.append(list,element('p','Wyłączenie pobierania ukrywa funkcje zapisu, ale osoba mająca dostęp do odtwarzania może skopiować obraz.'),apply);
    }
    usersBox.append(details);
  }
  $('ha-form').elements.url.value=ha.url;$('ha-form').elements.enabled.checked=ha.enabled;
  if(!storageRequest&&!storagePolling)renderStorage(storage);
  $('recording-segment').value=String(recording.segment_seconds||300);
  $('monitoring-form').elements.stale_after.value=monitoring.stale_after;
  $('monitoring-config-status').textContent=monitoring.invalid?'Nieprawidłowy zapis ustawienia - używany jest domyślny czas 900 sekund. Zapisz poprawną wartość.':'';


  $('storage-form').elements.minimum.value=storage.limits_invalid?'':storage.minimum_free/1e9;$('storage-form').elements.buffer.value=storage.limits_invalid?'':storage.free_buffer/1e9;
  $('storage-limit-status').textContent=storage.limits_invalid?'Nieprawidłowe limity dysku. Zapis i rotacja są wstrzymane. Wprowadź i zapisz poprawne wartości.':'';
  const cameraBox=$('manage-cameras');cameraBox.replaceChildren();
  for(const camera of adminCameras){const data=camera;
    const details=element('details',null,'camera-config');details.dataset.search=(camera.name+' '+camera.slug).toLowerCase();details.dataset.cameraId=String(camera.id);const summary=element('summary',camera.name+' · '+camera.recording.toUpperCase()+(camera.enabled?'':' · wyłączona'));details.append(summary);const stats=element('span',null,'camera-statistics');stats.append(element('span','Oczekiwanie na statystyki…','camera-statistics-note'));summary.append(stats);
    const name=labeledInput('Nazwa','text',camera.name),main=labeledInput('Nowy adres RTSP główny - puste zachowuje obecny','text'),sub=labeledInput('Nowy adres RTSP pomocniczy - puste zachowuje obecny','text');details.append(name.label);const manual=element('details');manual.append(element('summary','Ręczne adresy RTSP (zaawansowane)'),main.label,sub.label);
    const cameraUser=labeledInput('Nowy użytkownik kamery - puste zachowuje dane','text'),cameraPassword=labeledInput('Nowe hasło kamery','password');cameraPassword.input.autocomplete='new-password';details.append(cameraUser.label,cameraPassword.label);
    const clear=element('input');clear.type='checkbox';const clearLabel=element('label','Usuń strumień pomocniczy','inline');clearLabel.prepend(clear);manual.append(clearLabel);details.append(manual);
    const ip=labeledInput('Adres IP do ponownego wykrycia','text');ip.input.placeholder='192.168.0.100';details.append(ip.label);
    const rediscover=element('button','Wykryj ponownie strumienie');rediscover.type='button';const discoveryStatus=element('p');discoveryStatus.setAttribute('role','status');details.append(rediscover,discoveryStatus);
    rediscover.addEventListener('click',async()=>{if(rediscover.disabled)return;if(!ip.input.value.trim()){notify('Podaj adres IP kamery.');return;}if(!window.confirm('Wykryć ponownie i zastąpić dotychczasowe adresy strumieni? Podaj aktualne dane logowania kamery, jeśli są wymagane.'))return;rediscover.disabled=true;discoveryStatus.textContent='Wykrywanie strumieni...';try{const result=await api(`/api/cameras/${camera.id}`,'PATCH',{ip:ip.input.value.trim(),username:cameraUser.input.value,password:cameraPassword.input.value});cameraPassword.input.value='';invalidateDeviceInfo(camera);void loadDeviceInfo(camera,true,true).catch(()=>{});await refreshCameras();await refreshAdmin();notify(result.sub_configured?'Zaktualizowano oba strumienie.':'Zaktualizowano strumień główny; nie wykryto pomocniczego.');}catch(error){discoveryStatus.textContent=error.message;notify(error.message,'error');}finally{rediscover.disabled=false;}});
    const mode=element('select');for(const [value,text] of [['rtsp','RTSP'],['none','Bez zapisu']])mode.add(new Option(text,value));mode.value=data.recording;const modeLabel=element('label','Nagrywanie');modeLabel.append(mode);details.append(modeLabel);
    const save=element('button','Zapisz kamerę');save.type='button';save.addEventListener('click',async()=>{try{const values={name:name.input.value,recording:mode.value};if(main.input.value)values.main_url=main.input.value;if(clear.checked)values.sub_url='';else if(sub.input.value)values.sub_url=sub.input.value;if(cameraUser.input.value||cameraPassword.input.value){values.username=cameraUser.input.value;values.password=cameraPassword.input.value;}await api(`/api/cameras/${camera.id}`,'PATCH',values);cameraPassword.input.value='';invalidateDeviceInfo(camera);void loadDeviceInfo(camera,true,true).catch(()=>{});await refreshCameras();await refreshAdmin();notify('Kamera zapisana.');}catch(error){notify(error.message,'error');}});details.append(save);
    const enable=element('button',camera.enabled?'Wyłącz kamerę':'Włącz kamerę');enable.type='button';enable.addEventListener('click',async()=>{try{await api(`/api/cameras/${camera.id}`,'PATCH',{enabled:!camera.enabled});await refreshCameras();await refreshAdmin();}catch(error){notify(error.message,'error');}});details.append(enable);
    const onvif=camera.onvif;const clockDetails=element('details');clockDetails.append(element('summary','Kontrola czasu ONVIF'));
    const endpoint=labeledInput('Adres usługi ONVIF','url',onvif.url),onvifUser=labeledInput('Użytkownik ONVIF','text',onvif.username),onvifPassword=labeledInput(onvif.password_configured?'Nowe hasło ONVIF - puste zachowuje obecne':'Hasło ONVIF','password');
    endpoint.input.placeholder='http://192.168.1.10/onvif/device_service';clockDetails.append(endpoint.label,onvifUser.label,onvifPassword.label);
    const auth=element('select');auth.add(new Option('HTTP Digest','digest'));auth.add(new Option('WS-Security UsernameToken','wsse'));auth.value=onvif.auth_mode;const authLabel=element('label','Uwierzytelnianie ONVIF');authLabel.append(auth);clockDetails.append(authLabel,element('p','UsernameToken to starsza metoda o słabszych zabezpieczeniach. Używaj jej w zaufanej sieci lub przez HTTPS. Kamera może odmówić dostępu, jeśli jej czas znacznie różni się od czasu serwera.'));
    let clockStatus='Czas kamery nie został jeszcze sprawdzony.';
    if(onvif.checked!==null&&onvif.fresh===false)clockStatus='Brak aktualnego odczytu czasu ONVIF.';
    else if(onvif.error)clockStatus=onvif.error;else if(onvif.checked!==null&&onvif.offset!==null)clockStatus=`Różnica czasu: ${Number(onvif.offset).toFixed(1)} s. Sprawdzono: ${new Date(onvif.checked*1000).toLocaleString('pl-PL')}.`;
    clockDetails.append(element('p',clockStatus));const clockSave=element('button','Zapisz ustawienia ONVIF');clockSave.type='button';clockSave.addEventListener('click',async()=>{try{await api(`/api/cameras/${camera.id}/onvif`,'PATCH',{url:endpoint.input.value,username:onvifUser.input.value,password:onvifPassword.input.value,auth_mode:auth.value});onvifPassword.input.value='';invalidateDeviceInfo(camera);void loadDeviceInfo(camera,true,true).catch(()=>{});notify('Ustawienia ONVIF zapisane.');}catch(error){notify(error.message,'error');}});clockDetails.append(clockSave);details.append(clockDetails);
    if(camera.enabled&&onvif.url){
      const reboot=element('button','Uruchom ponownie kamerę');reboot.type='button';
      reboot.addEventListener('click',async()=>{if(reboot.disabled||!window.confirm('Uruchomić ponownie kamerę '+camera.name+'? Nastąpi przerwa w obrazie i nagrywaniu.'))return;reboot.disabled=true;try{await api(`/api/cameras/${camera.id}/reboot`,'POST',{confirm:true});notify('Kamera potwierdziła polecenie restartu.','success');}catch(error){notify(error.message,'error');}finally{reboot.disabled=false;}});
      const capabilities=element('button','Sprawdź usługi i profile ONVIF');capabilities.type='button';const capabilityStatus=element('p');
      capabilities.onclick=async()=>{capabilities.disabled=true;try{const data=await api(`/api/cameras/${camera.id}/onvif-capabilities`);capabilityStatus.textContent=[data.media1?'Media1':null,data.media2?'Media2':null,data.analytics?'Analytics':null,data.events?'Events':null].filter(Boolean).join(' · ')||'Brak rozpoznanych usług.';for(const profile of data.profiles){capabilityStatus.append(element('div',`${profile.encoding} ${profile.width}×${profile.height} · metadane: ${profile.metadata?'tak':'nie'} · audio wejście: ${profile.audio_input?'tak':'nie'} · audio wyjście: ${profile.audio_output&&profile.audio_decoder?'tak':'nie'}`));}if(data.media2_error)capabilityStatus.append(element('div','Media2 zgłoszony, odczyt profili nieudany.'));}catch(error){capabilityStatus.textContent=error.message;}finally{capabilities.disabled=false;}};
      const firmware=firmwareSettingsPanel(camera);reboot.hidden=true;firmware.hidden=true;
      details.addEventListener('toggle',()=>{if(details.open)loadDeviceInfo(camera).then(result=>{reboot.hidden=result.reboot!==true;firmware.hidden=result.firmware_upgrade!==true;},()=>{});});
      details.append(reboot,capabilities,capabilityStatus,firmware);
    }
    if(camera.enabled)details.append(deviceControlPanel(camera));details.append(eventSettingsPanel(camera),metadataSettingsPanel(camera));
    const remove=element('button','Usuń kamerę z konfiguracji');remove.type='button';remove.addEventListener('click',async()=>{if(!window.confirm('Usunąć kamerę z konfiguracji? Pliki nagrań pozostaną na dysku.'))return;try{await api(`/api/cameras/${camera.id}`,'DELETE');await refreshCameras();await refreshAdmin();}catch(error){notify(error.message,'error');}});details.append(remove);cameraBox.append(details);
  }
  $('overview-cameras').textContent=adminCameras.filter(c=>c.enabled).length+' / '+adminCameras.length;$('overview-rtsp').textContent=String(adminCameras.filter(c=>c.enabled&&c.recording==='rtsp').length);filterAdminCameras();
  void refreshCameraStatistics(generation);
  const events=audit.events;$('audit').replaceChildren();for(const event of events)$('audit').append(element('p',new Date(event.timestamp*1000).toLocaleString()+' | '+event.user_id+' | '+event.action+' | '+event.object_type+' '+event.object_id));
  const results=await Promise.allSettled([refreshDomain(),refreshTLS(),refreshRotation(),refreshServices(),refreshUpgradeStatus()]);
  if(generation!==adminRefreshGeneration)return;
  const upgrade=results[4];if(upgrade.status==='fulfilled'&&(upgrade.value.pending||['verifying','applying','recovery_required'].includes(upgrade.value.status.state)))watchUpgrade();
  const failed=results.find(result=>result.status==='rejected');if(failed)notify('Nie udało się odświeżyć części statusów: '+failed.reason.message);
}

// Periodically confirm the identity and privileges displayed by the panel.
// Reload on a security-context change so no previous account's player,
// archive, exports, credentials or pending callbacks remain in the DOM.
async function refreshUserContext(){
  const previous=me;if(!previous)return false;
  let current;
  try{current=await api('/api/me');}
  catch(error){if(error.status===401&&me===previous){location.reload();return false;}throw error;}
  if(me!==previous)return false;
  if(current.id!==previous.id||current.role!==previous.role){location.reload();return false;}
  if(!$('archive-view').hidden||!$('sync-view').hidden){
    const incoming=(await api('/api/cameras')).cameras;
    if(me!==previous)return false;
    const grants=list=>JSON.stringify(list.map(c=>[c.id,c.may_download]).sort((a,b)=>a[0]-b[0]));
    if(grants(cameras)!==grants(incoming)){location.reload();return false;}
  }
  return true;
}

async function start() {
  try {me=await api('/api/me');}
  catch(error) {me=null;$('signed-in-user').textContent='';$('login-panel').hidden=false;$('application').hidden=true;$('app-nav').hidden=true;$('brand-live').disabled=true;$('signed-in-user').hidden=true;$('account-options').hidden=true;if(error.status!==401)notify(error.message,'error');try{$('setup-panel').hidden=!(await api('/api/setup')).available;}catch{$('setup-panel').hidden=true;}return;}
  $('login-panel').hidden=true;$('application').hidden=false;$('app-nav').hidden=false;$('brand-live').disabled=false;$('signed-in-user').hidden=false;$('show-admin').hidden=me.role!=='admin';$('signed-in-user').textContent=me.username+' - '+(me.role==='admin'?'administrator':'użytkownik');view('cameras');
  renderCameras();
  try{await refreshCameras();}catch(error){if(cameraListState!=='ready'){cameraListState='error';renderCameras();}notify('Nie udało się wczytać pełnego widoku: '+error.message);}
}
$('refresh-cameras').addEventListener('click',async()=>{try{await refreshCameras();notify('Lista kamer odświeżona.','success');}catch(error){if(cameraListState!=='ready'){cameraListState='error';renderCameras();}notify(error.message,'error');}});
$('login-form').addEventListener('submit',async(event)=>{event.preventDefault();try{notify('');await api('/api/login','POST',formData(event.target));event.target.reset();await start();}catch(e){notify(e.message);}});
$('setup-form').addEventListener('submit',async(event)=>{event.preventDefault();const value=formData(event.target);
  if(value.password!==value.confirmation){notify('Hasła nie są identyczne.');return;}
  try{await api('/api/setup','POST',{token:value.token,username:value.username,password:value.password});event.target.reset();$('setup-panel').hidden=true;notify('Administrator utworzony. Możesz się zalogować.');}
  catch(error){notify(error.message,'error');}
});
function closeAccountMenu(){ $('account-options').hidden=true;$('signed-in-user').setAttribute('aria-expanded','false'); }
$('signed-in-user').addEventListener('click',()=>{const open=$('account-options').hidden;$('account-options').hidden=!open;$('signed-in-user').setAttribute('aria-expanded',String(open));});
if(typeof document.addEventListener==='function'){document.addEventListener('pointerdown',event=>{if(!$('account-menu').contains(event.target))closeAccountMenu();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('account-options').hidden){closeAccountMenu();$('signed-in-user').focus();}});}
$('logout').addEventListener('click',async()=>{stopTalkback();try{await flushLiveWorkspace();await api('/api/logout','POST',{});location.reload();}catch(e){notify(e.message);}});
$('own-password-form').addEventListener('submit',async(event)=>{
  event.preventDefault();const form=event.target;
  if(form.elements.new.value!==form.elements.confirmation.value){notify('Nowe hasła nie są identyczne.');return;}
  try{await api('/api/account/password','POST',{current:form.elements.current.value,new:form.elements.new.value});form.reset();location.reload();}
  catch(error){notify(error.message,'error');}
});
$('layout-select').addEventListener('change',()=>{renderCameras();queueLiveWorkspace();});
$('layout-form').addEventListener('submit',async(event)=>{event.preventDefault();try{const name=event.target.elements.name.value;const ids=Array.from($('layout-cameras').querySelectorAll('input:checked')).map(x=>Number(x.value));await api('/api/layouts','POST',{name,cameras:ids});event.target.reset();await refreshLayouts();notify('Układ zapisany.');}catch(error){notify(error.message,'error');}});
$('manage-layout-select').addEventListener('change',()=>{$('delete-layout').disabled=!$('manage-layout-select').value;});
$('delete-layout').addEventListener('click',async()=>{const id=$('manage-layout-select').value;if(!id)return;if(!window.confirm('Usunąć wybrany układ kamer?'))return;try{await api(`/api/layouts/${id}`,'DELETE');await refreshLayouts();renderCameras();queueLiveWorkspace();notify('Układ usunięty.');}catch(error){notify(error.message,'error');}});
let analyticsRefreshGeneration=0,faceEnrollmentId=null,faceEnrollmentBusy=false;
function resetFaceEnrollment(){
  faceEnrollmentId=null;$('face-enrollment-title').textContent='Dodaj profil twarzy';$('face-enrollment-label').value='';$('face-enrollment-images').value='';$('face-enrollment-consent').checked=false;$('face-enrollment-status').textContent='';
}
function editFaceEnrollment(profile){
  if(faceEnrollmentBusy)return;
  resetFaceEnrollment();faceEnrollmentId=profile.id;$('face-enrollment-title').textContent='Aktualizuj zdjęcia: '+profile.label;$('face-enrollment-label').value=profile.label;
  if($('face-enrollment-form').scrollIntoView)$('face-enrollment-form').scrollIntoView({block:'nearest'});
}
function faceFileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>{const result=String(reader.result);resolve(result.slice(result.indexOf(',')+1));};reader.onerror=()=>reject(new Error('Nie można odczytać zdjęcia.'));reader.readAsDataURL(file);});}
$('face-enrollment-cancel').addEventListener('click',()=>{if(!faceEnrollmentBusy)resetFaceEnrollment();});
$('face-enrollment-form').addEventListener('submit',async event=>{
  event.preventDefault();if(faceEnrollmentBusy)return;
  const status=$('face-enrollment-status'),files=Array.from($('face-enrollment-images').files||[]),label=$('face-enrollment-label').value.trim(),consent=$('face-enrollment-consent').checked;
  if(!label||!consent||files.length<1||files.length>5||files.some(f=>!f.size||f.size>8*1024*1024)||files.reduce((n,f)=>n+f.size,0)>20*1024*1024){status.textContent='Podaj nazwę, zgodę i od 1 do 5 zdjęć w podanym limicie.';return;}
  if(faceEnrollmentId&&!window.confirm('Zastąpić wszystkie dotychczasowe próbki tego profilu nowymi?'))return;
  const id=faceEnrollmentId;faceEnrollmentBusy=true;for(const key of ['label','images','consent','save','cancel'])$('face-enrollment-'+key).disabled=true;
  status.textContent='Przesyłanie i analiza zdjęć...';
  try{
    const images=[];for(const file of files)images.push(await faceFileBase64(file));
    await api('/api/admin/analytics/faces'+(id?'/'+encodeURIComponent(id):''),id?'PUT':'POST',{label,consent,images});
    resetFaceEnrollment();await refreshAnalyticsAdmin();status.textContent=id?'Zdjęcia profilu zaktualizowane.':'Profil zapisany.';
  }catch(error){status.textContent=error.message;}
  finally{faceEnrollmentBusy=false;for(const key of ['label','images','consent','save','cancel'])$('face-enrollment-'+key).disabled=false;}
});
async function refreshAnalyticsAdmin(){
  const generation=++analyticsRefreshGeneration,button=$('analytics-refresh'),status=$('analytics-status');
  button.disabled=true;status.textContent='Sprawdzanie...';$('analytics-models').replaceChildren();$('analytics-profiles').replaceChildren();
  try{
    const result=await api('/api/admin/analytics');
    if(generation!==analyticsRefreshGeneration)return;
    const names={coco:'COCO (80 klas)','face-detector':'YuNet (detekcja twarzy)','face-recognizer':'SFace (dopasowanie profili)'},states={present:'Plik dostępny - inferencja niezweryfikowana',missing:'Brak pliku',untrusted:'Nieprawidłowy lub niezaufany plik'};
    for(const [key,name] of Object.entries(names)){
      const item=result.models?.[key];$('analytics-models').append(element('p',name+': '+(states[item?.state]||'Nieznany stan')));
    }
    const profiles=$('analytics-profiles');profiles.append(element('h3','Zapisane profile twarzy'));
    if(!result.profiles.length)profiles.append(element('p','Brak zapisanych profili.'));
    for(const profile of result.profiles){
      const row=element('div',null,'timeline-actions'),remove=element('button','Usuń profil');remove.type='button';
      const edit=element('button','Aktualizuj zdjęcia');edit.type='button';edit.addEventListener('click',()=>editFaceEnrollment(profile));
      row.append(element('span',profile.label+' - próbki: '+profile.samples),remove,edit);profiles.append(row);
      remove.addEventListener('click',async()=>{
        if(faceEnrollmentBusy)return;
        if(!window.confirm('Usunąć profil i jego szablony? Historyczna etykieta przestanie być wyświetlana.'))return;
        remove.disabled=true;try{await api('/api/admin/analytics/faces/'+encodeURIComponent(profile.id),'DELETE');if(faceEnrollmentId===profile.id)resetFaceEnrollment();await refreshAnalyticsAdmin();}catch(error){status.textContent=error.message;remove.disabled=false;}
      });
    }
    status.textContent='Stan odczytany. Sprawdzenie inferencji: analytics-model-status --verify (lokalny root).';
  }catch(error){if(generation===analyticsRefreshGeneration)status.textContent=error.message;}
  finally{if(generation===analyticsRefreshGeneration)button.disabled=false;}
}
$('analytics-refresh').addEventListener('click',refreshAnalyticsAdmin);
const adminSections=['overview','cameras','users','storage','network','integrations','system','audit'];
function selectAdminSection(section){
  if(!adminSections.includes(section))return;
  for(const panel of document.querySelectorAll('[data-admin-section]'))panel.hidden=panel.dataset.adminSection!==section;
  for(const name of adminSections){const button=$('admin-tab-'+name);if(name===section)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
}
for(const section of adminSections)$('admin-tab-'+section).addEventListener('click',()=>selectAdminSection(section));
function openAdminCamera(id){for(const card of $('manage-cameras').querySelectorAll('.camera-config'))if(card.dataset.cameraId===String(id)){card.open=true;card.hidden=false;if(card.scrollIntoView)card.scrollIntoView({block:'nearest'});}}
function filterAdminCameras(){const search=$('camera-admin-search').value.trim().toLowerCase();for(const card of $('manage-cameras').querySelectorAll('.camera-config'))card.hidden=!card.dataset.search.includes(search);}
$('camera-admin-search').addEventListener('input',filterAdminCameras);
if($('camera-statistics-refresh'))$('camera-statistics-refresh').addEventListener('click',()=>refreshCameraStatistics(adminRefreshGeneration));
setInterval(()=>{if(me&&me.role==='admin'&&!document.hidden&&!$('admin-view').hidden)void refreshCameraStatistics(adminRefreshGeneration);},60000);
async function enterArchive(){try{const camera=cameras.find(c=>activeCamera&&c.id===activeCamera.id)||cameras[0];if(!camera){notify('Brak dostępnych kamer.');return;}await openArchive(camera);}catch(error){notify(error.message,'error');}}
$('show-archive').addEventListener('click',enterArchive);
$('sync-back-archive').addEventListener('click',enterArchive);
$('archive-sync').addEventListener('click',openSync);
$('archive-camera-select').addEventListener('change',async()=>{const camera=cameras.find(c=>String(c.id)===$('archive-camera-select').value);if(camera)await openArchive(camera);});
function enterLive(){if(!me)return;notify('');view('cameras');}
$('show-cameras').addEventListener('click',enterLive);
$('brand-live').addEventListener('click',enterLive);
$('show-settings').addEventListener('click',()=>{closeAccountMenu();notify('');view('settings');refreshExports();});
$('back-cameras').addEventListener('click',()=>{ $('archive-player').pause();view('cameras');});
$('show-sync').addEventListener('click',openSync);
$('show-admin').addEventListener('click',async()=>{try{if(!me||me.role!=='admin')return;view('admin');selectAdminSection('overview');await refreshAdmin();}catch(e){notify(e.message);}});
$('load-archive').addEventListener('click',()=>loadArchive(true));
$('archive-date').addEventListener('change',()=>loadArchive(true));
function containsInstant(record,instant){
  return [record.utc_first,record.utc_second].some(value=>value&&Date.parse(value)/1000<=instant&&instant<Date.parse(value)/1000+record.duration);
}
for(const button of document.querySelectorAll('[data-recent]'))button.addEventListener('click',async()=>{
  const target=Date.now()/1000-Number(button.dataset.recent)*60;$('archive-date').value=cameraDay(target*1000);
  if(!await loadArchive())return;
  const index=records.findIndex(record=>containsInstant(record,target));
  if(index<0){notify('Brak ukończonego nagrania dla wybranego czasu.');return;}
  if(records[index].utc_second){notify('Czas nagrania jest niejednoznaczny po zmianie czasu.');return;}
  playRecord(index,target-Date.parse(records[index].utc_first)/1000);
});
async function playNextRecording(){
  const current=records[recordPosition];if(!current||!current.utc_first)return;
  if(current.utc_second){notify('Nie można automatycznie przejść przez niejednoznaczną godzinę po zmianie czasu.');return;}
  const end=Date.parse(current.utc_first)/1000+current.duration;
  let next=records.findIndex((record,index)=>index>recordPosition&&record.utc_first&&Date.parse(record.utc_first)/1000+record.duration>end+0.05);
  if(next<0){
    const generation=playbackGeneration,camera=activeCamera,date=$('archive-date').value;
    const upcoming=await api(`/api/cameras/${camera.id}/next-recording?after_ms=${Math.floor(end*1000)}&exclude=${current.id}`);
    if(generation!==playbackGeneration||camera!==activeCamera||date!==$('archive-date').value)return;
    const candidate=upcoming.recording;
    if(!candidate)return;
    if(candidate.utc_second){notify('Kolejne nagranie ma niejednoznaczną godzinę po zmianie czasu.');return;}
    $('archive-date').value=cameraDay(Date.parse(candidate.utc_first));if(!await loadArchive())return;
    next=records.findIndex(record=>record.id===candidate.id);
  }
  if(next<0)return;
  const record=records[next];if(record.utc_second){notify('Kolejne nagranie ma niejednoznaczną godzinę po zmianie czasu.');return;}
  const start=Date.parse(record.utc_first)/1000;if(start>end+1)notify('Między nagraniami występuje przerwa.');
  playRecord(next,Math.max(0,end-start));
}
$('playback-speed').addEventListener('change',event=>{$('archive-player').playbackRate=Number(event.target.value);});
async function seekArchive(delta){
  const record=records[recordPosition],player=$('archive-player');if(!record||!player.currentSrc||!record.utc_first)return;
  if(record.utc_second){notify('Nie można przewijać przez niejednoznaczną godzinę po zmianie czasu.');return;}
  const paused=archiveSwitch?.paused??(player.paused===true);
  const target=(archiveSwitch?.target??(Date.parse(record.utc_first)/1000+player.currentTime))+delta;
  if(!Number.isFinite(target))return;
  const day=cameraDay(target*1000),camera=activeCamera;
  if(day!==$('archive-date').value){$('archive-date').value=day;if(!await loadArchive()||activeCamera!==camera)return;}
  const index=records.findIndex(r=>containsInstant(r,target));
  if(index<0){notify('Brak nagrania w wybranym czasie.');return;}
  if(records[index].utc_second){notify('Wybrany czas jest niejednoznaczny po zmianie czasu.');return;}
  const offset=target-Date.parse(records[index].utc_first)/1000;
  if(records[index].id===record.id&&player.currentSrc&&!archiveSwitch)player.currentTime=offset;else playRecord(index,offset,false,paused);
}
for(const button of document.querySelectorAll('[data-seek]'))button.addEventListener('click',()=>{seekArchive(Number(button.dataset.seek)).catch(error=>notify(error.message,'error'));});
$('camera-form').addEventListener('submit',async(event)=>{event.preventDefault();const button=$('camera-discover-submit'),status=$('camera-discover-status');if(button.disabled)return;button.disabled=true;status.textContent='Wykrywanie strumieni wideo...';try{const result=await api('/api/cameras','POST',formData(event.target));void api(`/api/cameras/${result.id}/settings?refresh=1`).catch(()=>{});event.target.reset();status.textContent=result.sub_configured?'Wykryto strumień główny i pomocniczy.':'Kamera dodana ze strumieniem głównym (brak potwierdzonego pomocniczego).';await Promise.all([refreshCameras(),refreshAdmin()]);openAdminCamera(result.id);notify('Kamera dodana.');}catch(e){status.textContent=e.message;notify(e.message);}finally{button.disabled=false;}});
$('camera-manual-form').addEventListener('submit',async(event)=>{event.preventDefault();try{const result=await api('/api/cameras','POST',formData(event.target));void api(`/api/cameras/${result.id}/settings?refresh=1`).catch(()=>{});event.target.reset();await Promise.all([refreshCameras(),refreshAdmin()]);openAdminCamera(result.id);notify('Kamera dodana.');}catch(e){notify(e.message);}});
$('user-form').addEventListener('submit',async(event)=>{event.preventDefault();try{await api('/api/users','POST',formData(event.target));event.target.reset();await refreshAdmin();notify('Użytkownik dodany.');}catch(e){notify(e.message);}});
async function refreshUpgradeStatus(){
  const value=await api('/api/updates');const status=value.status;
  $('upgrade-status').textContent='Wersja '+(value.version||'')+' | '+(value.pending?'Aktualizacja oczekuje na uruchomienie.':status.message||'Brak oczekującej aktualizacji.');
  const select=$('upgrade-form').elements.package,chosen=select.value;
  select.replaceChildren();for(const name of (value.packages||[])){const option=element('option',name);option.value=name;select.append(option);}
  if((value.packages||[]).includes(chosen))select.value=chosen;
  $('upgrade-form').querySelector('button[type="submit"]').disabled=value.pending||!(value.packages||[]).length;
  const rollback=$('rollback-form');if(rollback){const point=rollback.elements.point,previous=point.value;point.replaceChildren();for(const row of (value.restore_points||[])){const option=element('option',row.version+' - '+new Date(row.created*1000).toLocaleString('pl-PL'));option.value=row.id;point.append(option);}if((value.restore_points||[]).some(r=>r.id===previous))point.value=previous;rollback.dataset.version=value.version;rollback.querySelector('button').disabled=value.pending||['applying','verifying','recovery_required'].includes(status.state)||!point.options.length;}
  return value;
}
async function refreshDomain(){
  const value=await api('/api/settings/domain');
  $('domain-current').textContent='Aktualny adres: '+value.origin;
  $('domain-form').querySelector('button').disabled=!value.supported||value.pending||['applying','recovery_required'].includes(value.status.state);
  $('domain-status').textContent=!value.supported?'Ta instalacja wymaga migracji mechanizmu odzyskiwania przed zmianą domeny.':value.pending?'Zmiana domeny oczekuje na przetworzenie.':value.status.message||'Brak oczekującej zmiany domeny.';
  return value;
}
$('domain-refresh').addEventListener('click',async()=>{try{await refreshDomain();}catch(error){notify(error.message,'error');}});
$('domain-form').addEventListener('submit',async(event)=>{
  event.preventDefault();const form=event.target,button=form.querySelector('button');let queued=false;button.disabled=true;
  try{await api('/api/settings/domain','POST',{hostname:form.elements.hostname.value});queued=true;form.reset();await refreshDomain();notify('Zmiana domeny oczekuje na sprawdzenie.');}
  catch(error){notify(error.message,'error');}finally{if(!queued)button.disabled=false;}
});
async function refreshTLS(){
  const value=await api('/api/settings/tls');$('tls-status').textContent=value.pending?'Zmiana certyfikatu oczekuje na przetworzenie.':value.status.message||'Brak oczekującej zmiany certyfikatu.';
  return value;
}
$('tls-refresh').addEventListener('click',async()=>{try{await refreshTLS();}catch(error){notify(error.message,'error');}});
$('tls-form').addEventListener('submit',async(event)=>{
  event.preventDefault();const form=event.target,button=form.querySelector('button');button.disabled=true;
  try{
    const cert=form.elements.certificate.files[0],key=form.elements.key.files[0];
    if(!cert||!key||cert.size+key.size>80*1024)throw new Error('Wybierz certyfikat i klucz o łącznym rozmiarze do 80 KiB.');
    await api('/api/settings/tls','POST',{certificate:await cert.text(),key:await key.text()});form.reset();await refreshTLS();
    notify('Certyfikat oczekuje na sprawdzenie i instalację.');
  }catch(error){notify(error.message,'error');}finally{button.disabled=false;}
});
let upgradePollTimer=null;
function watchUpgrade(){
  if(upgradePollTimer!==null)return;
  const poll=async()=>{
    upgradePollTimer=null;if(!me||$('admin-view').hidden)return;
    try{
      const result=await refreshUpgradeStatus();
      if(!result.pending&&!['verifying','applying','recovery_required'].includes(result.status.state))return;
    }catch{$('upgrade-status').textContent='Serwer jest niedostępny. Oczekiwanie na zakończenie aktualizacji...';}
    upgradePollTimer=setTimeout(poll,5000);
  };
  upgradePollTimer=setTimeout(poll,5000);
}
async function refreshRotation(){
  const data=await api('/api/rotation'),labels={unknown:'Brak zapisanych operacji',rotation_ok:'Rotacja działa poprawnie',rotation_blocked:'Nie udało się odzyskać wymaganego miejsca',rotation_error:'Błąd rotacji'};
  const latest=data.history[0];let status=labels[data.state]||'Brak danych';
  if(data.state==='rotation_blocked'&&latest&&latest.state==='rotation_blocked'&&latest.protected_count)
    status+=`. Pominięto ${latest.protected_count} chronionych plików (${(latest.protected_bytes/1e9).toFixed(2)} GB).`;
  $('rotation-state').textContent=status;const box=$('rotation-history');box.replaceChildren();
  for(const item of data.history){const before=item.before===null?'-':(item.before/1e9).toFixed(2)+' GB',after=item.after===null?'-':(item.after/1e9).toFixed(2)+' GB';box.append(element('p',`${new Date(item.created*1000).toLocaleString('pl-PL')} | ${labels[item.state]||item.state} | usunięto: ${item.files_count} | wolne: ${before} do ${after} | chronione: ${item.protected_count} (${(item.protected_bytes/1e9).toFixed(2)} GB)`));}
  if(!data.history.length)box.append(element('p','Brak historii rotacji.'));
}
async function refreshServices(){
  const value=await api('/api/system/health'),box=$('service-status');box.replaceChildren();
  const labels={active:'działa',inactive:'awaria lub usługa zatrzymana',unknown:'brak danych'};
  for(const service of value.services)box.append(element('p',`${service.name}: ${service.fresh===false?'brak aktualnych danych':(labels[service.state]||'brak danych')}`));
  if(!value.services.length)box.append(element('p','Oczekiwanie na pierwsze sprawdzenie usług.'));
  const problems={disk_unavailable:'Dysk nagrań niedostępny',disk_low:'Mało wolnego miejsca',rotation_blocked:'Rotator nie może zwolnić wymaganego miejsca',rotation_error:'Błąd rotatora',monitor_error:'Nie można sprawdzić usług',stream_service_error:'Serwer strumieni niedostępny',ha_delivery_error:'Ostatnia próba wysłania powiadomienia do Home Assistanta nie powiodła się'};
  for(const item of value.system){
    if(problems[item.state])box.append(element('p',problems[item.state]));
    else if(item.name.startsWith('clock:')&&item.fresh===false){
      const camera=cameras.find(c=>String(c.id)===item.name.slice(6));
      box.append(element('p','Brak aktualnego odczytu czasu ONVIF: '+(camera?camera.name:item.name.slice(6))));
    }
    else if(item.state==='clock_skew'&&item.name.startsWith('clock:')){
      const camera=cameras.find(c=>String(c.id)===item.name.slice(6));
      box.append(element('p','Różnica czasu kamery: '+(camera?camera.name:item.name.slice(6))));
    }
    else if(item.state==='clock_unavailable'&&item.name.startsWith('clock:')){
      const camera=cameras.find(c=>String(c.id)===item.name.slice(6));
      box.append(element('p','Nie można odczytać czasu ONVIF: '+(camera?camera.name:item.name.slice(6))));
    }
    else if(item.state==='recording_corrupt'&&item.name.startsWith('recorder_corrupt:')){
      const slug=item.name.slice('recorder_corrupt:'.length),camera=cameras.find(c=>c.slug===slug);
      box.append(element('p','Nie można odczytać segmentu RTSP: '+(camera?camera.name:slug)+'. Plik pozostawiono do sprawdzenia.'));
    }
    else if(item.state==='recording_publish_error'&&item.name.startsWith('recorder_publish:')){
      const slug=item.name.slice('recorder_publish:'.length),camera=cameras.find(c=>c.slug===slug);
      box.append(element('p','Nie można zapisać segmentu RTSP w archiwum: '+(camera?camera.name:slug)+'. Plik pozostawiono w katalogu roboczym.'));
    }
    else if(item.state==='recording_staging_error'&&item.name.startsWith('recorder_staging:')){
      const slug=item.name.slice('recorder_staging:'.length),camera=cameras.find(c=>c.slug===slug);
      box.append(element('p','Nie można przygotować katalogu zapisu RTSP: '+(camera?camera.name:slug)+'. Pozostałe kamery pracują niezależnie.'));
    }
    else if(item.state==='recording_audio_missing'&&item.name.startsWith('recorder_audio:')){
      const slug=item.name.slice('recorder_audio:'.length),camera=cameras.find(c=>c.slug===slug);
      box.append(element('p','Ostatnio sprawdzony segment nie ma ścieżki audio: '+(camera?camera.name:slug)+'. Obraz został zachowany.'));
    }
    else if(item.state==='recording_process_error'&&item.name.startsWith('recorder_process:')){
      const slug=item.name.slice('recorder_process:'.length),camera=cameras.find(c=>c.slug===slug);
      box.append(element('p','Błąd procesu nagrywania RTSP: '+(camera?camera.name:slug)+'. Rejestrator ponawia próbę.'));
    }
  }
}
setInterval(async()=>{
  if(!me||document.hidden||statusRefreshPending)return;
  statusRefreshPending=true;
  try{
    if(!await refreshUserContext())return;
    if(!$('camera-view').hidden)await refreshCameraStatus();
    if(me.role==='admin'&&!$('admin-view').hidden)await refreshServices();
  }catch(error){notify(error.message,'error');}finally{statusRefreshPending=false;}
},30000);
$('service-refresh').addEventListener('click',async()=>{try{await refreshServices();}catch(error){notify(error.message,'error');}});
$('monitoring-form').addEventListener('submit',async(event)=>{
  event.preventDefault();const value=Number(event.target.elements.stale_after.value);
  if(!Number.isInteger(value)||value<60||value>86400){notify('Czas alarmu musi wynosić od 60 do 86400 sekund.');return;}
  try{await api('/api/settings/monitoring','PATCH',{stale_after:value});$('monitoring-config-status').textContent='';notify('Czas alarmu zapisany.');}catch(error){notify(error.message,'error');}
});
$('rotation-preview').addEventListener('click',async()=>{
  const button=$('rotation-preview'),box=$('rotation-preview-result');button.disabled=true;
  try{
    const job=await api('/api/rotation/previews','POST',{});box.replaceChildren(element('p','Podgląd w kolejce.'));
    const poll=async()=>{
      try{
        const value=await api(`/api/rotation/previews/${job.id}`);
        if(value.status==='queued'||value.status==='running'){box.replaceChildren(element('p',value.status==='queued'?'Podgląd w kolejce.':'Skanowanie nagrań...'));setTimeout(poll,3000);return;}
        button.disabled=false;
        if(value.status!=='done'){box.replaceChildren(element('p',value.error||'Podgląd nie powiódł się.'));return;}
        const result=value.result;box.replaceChildren(element('p',`Do usunięcia: ${result.files_count} plików. Szacowane wolne miejsce: ${(result.before/1e9).toFixed(2)} do ${(result.after/1e9).toFixed(2)} GB.`));
        if(result.protected_count)box.append(element('p',`Chronione pliki: ${result.protected_count} (${(result.protected_bytes/1e9).toFixed(2)} GB). Nie zostaną usunięte.`));
        if(!result.reached_target)box.append(element('p','Nie osiągnięto zapasu - brak dostatecznej liczby plików możliwych do usunięcia.'));
        if(result.unknown_time_count)box.append(element('p',`Pominięto ${result.unknown_time_count} plików (${(result.unknown_time_bytes/1e9).toFixed(2)} GB) z nieistniejącym czasem lokalnym.`));
        if(result.files.length){const list=element('details');list.append(element('summary',result.truncated?'Pierwsze 500 plików':'Pliki do usunięcia'));for(const name of result.files)list.append(element('p',name));box.append(list);}
      }catch(error){button.disabled=false;notify(error.message,'error');}
    };await poll();
  }catch(error){button.disabled=false;notify(error.message,'error');}
});
async function uploadUpdateFile(kind,file){
  const response=await fetch('/api/update-uploads/'+kind,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':cookie('sunhill_csrf')},body:file});
  const value=await response.json();if(!response.ok)throw new Error(value.error||'Nie udało się przesłać pliku.');return value.id;
}
$('upgrade-refresh').addEventListener('click',async()=>{try{await refreshUpgradeStatus();}catch(error){notify(error.message,'error');}});
$('upgrade-form').addEventListener('submit',async(event)=>{
  event.preventDefault();const form=event.target,button=form.querySelector('button[type="submit"]');
  const name=form.elements.package.value;if(!name)return;
  button.disabled=true;
  try{await api('/api/updates','POST',{package:name});watchUpgrade();await refreshUpgradeStatus();notify('Aktualizacja oczekuje na instalację.');}
  catch(error){notify(error.message,'error');button.disabled=false;}
});
$('rollback-form')?.addEventListener('submit',async event=>{event.preventDefault();const form=event.target,button=form.querySelector('button');if(!form.elements.confirm.checked||!form.elements.point.value)return;button.disabled=true;try{await api('/api/updates','POST',{restore_point:form.elements.point.value,expected:form.dataset.version,confirm:true});form.elements.confirm.checked=false;watchUpgrade();await refreshUpgradeStatus();notify('Przywrócenie wersji oczekuje na wykonanie.');}catch(error){notify(error.message,'error');button.disabled=false;}});
let syncItems=[],syncTimer=null,syncSeconds=0,syncStarted=0,syncBase=0,syncPlaying=false,syncDay='',syncGeneration=0,syncLoading=null;
function stopSync(){syncGeneration++;syncLoading=null;if(syncTimer)clearInterval(syncTimer);syncTimer=null;syncPlaying=false;for(const item of syncItems){item.active=null;item.video.pause();item.video.removeAttribute('src');}syncItems=[];$('sync-grid').replaceChildren();}
function openSync(){
  view('sync');$('sync-zone').textContent='Strefa kamer: '+me.timezone;$('sync-start').value=cameraDay()+'T12:00:00';
  const box=$('sync-cameras');box.replaceChildren();
  for(const camera of cameras){const label=element('label');const input=element('input');input.type='checkbox';input.value=String(camera.id);input.checked=true;label.append(input,document.createTextNode(camera.name));box.append(label);}
}
function syncClock(epoch){
  return new Intl.DateTimeFormat('pl-PL',{timeZone:me.timezone,dateStyle:'short',timeStyle:'medium'}).format(new Date(epoch*1000));
}
async function prepareSync(){
  stopSync();const value=$('sync-start').value;if(!value)throw new Error('Wybierz datę i czas.');
  const ids=Array.from($('sync-cameras').querySelectorAll('input:checked')).map(x=>Number(x.value));
  if(!ids.length)throw new Error('Wybierz co najmniej jedną kamerę.');
  const generation=syncGeneration,fold=$('sync-fold').value;
  const instant=await api('/api/time/resolve','POST',{local:value,fold:fold===''?null:Number(fold)});
  if(generation!==syncGeneration)return;
  const initial=Date.parse(instant.utc)/1000;if(!Number.isFinite(initial))throw new Error('Nieprawidłowy czas serwera.');
  const day=cameraDay(initial*1000),grid=$('sync-grid');
  const selected=ids.map(id=>cameras.find(c=>c.id===id)).filter(Boolean);
  const lists=await Promise.all(selected.map(camera=>archivePages(`/api/cameras/${camera.id}/recordings?date=${encodeURIComponent(day)}`,'recordings',()=>generation===syncGeneration)));
  if(generation!==syncGeneration)return;
  syncSeconds=initial;syncDay=day;
  for(let i=0;i<selected.length;i++){const camera=selected[i],list=lists[i].recordings;
    const box=element('article',null,'camera');const title=element('div',camera.name,'camera-heading');const video=element('video');video.controls=true;video.playsInline=true;video.muted=true;video.className='sync-video';const status=element('p','Gotowe','camera-actions');box.append(title,video,status);grid.append(box);
    syncItems.push({id:camera.id,video,status,records:list,active:null});
  }
  $('sync-clock').textContent=syncClock(syncSeconds);
}
async function loadSyncDay(day){
  if(syncLoading)return;const generation=syncGeneration,items=syncItems.slice();syncLoading=day;
  for(const item of items){item.active=null;item.video.pause();item.video.removeAttribute('src');item.status.textContent='Wczytywanie nagrań';}
  try{
    const lists=await Promise.all(items.map(item=>archivePages(`/api/cameras/${item.id}/recordings?date=${encodeURIComponent(day)}`,'recordings',()=>generation===syncGeneration)));
    if(generation!==syncGeneration)return;
    for(let i=0;i<items.length;i++)items[i].records=lists[i].recordings;
    syncDay=day;
  }catch(error){if(generation===syncGeneration){pauseSync();notify(error.message,'error');}}
  finally{if(generation===syncGeneration)syncLoading=null;}
}
function tickSync(){
  if(!syncPlaying)return;
  syncSeconds=syncBase+(performance.now()-syncStarted)/1000;
  const day=cameraDay(syncSeconds*1000);$('sync-clock').textContent=syncClock(syncSeconds);
  if(day!==syncDay){loadSyncDay(day);return;}
  for(const item of syncItems){
    const record=item.records.find(r=>containsInstant(r,syncSeconds));
    if(!record||record.utc_second){if(item.active!==null){item.video.pause();item.video.removeAttribute('src');item.active=null;}item.status.textContent=record?'Niejednoznaczny czas po zmianie czasu':'Brak nagrania';continue;}
    const offset=Math.max(0,syncSeconds-Date.parse(record.utc_first)/1000);
    if(item.active!==record.id){item.active=record.id;item.video.src=`/api/recordings/${record.id}/file`;item.video.addEventListener('loadedmetadata',()=>{if(syncPlaying&&item.active===record.id&&syncItems.includes(item)){item.video.currentTime=offset;item.video.play().catch(()=>{});}},{once:true});}
    else if(item.video.readyState>=1){if(Math.abs(item.video.currentTime-offset)>1.5)item.video.currentTime=offset;if(item.video.paused)item.video.play().catch(()=>{});}
    item.status.textContent='Odtwarzanie';
  }
}
function pauseSync(){if(syncPlaying)syncSeconds=syncBase+(performance.now()-syncStarted)/1000;syncPlaying=false;if(syncTimer)clearInterval(syncTimer);syncTimer=null;for(const item of syncItems)item.video.pause();}
$('sync-load').addEventListener('click',async()=>{try{await prepareSync();notify('Widok przygotowany.');}catch(error){notify(error.message,'error');}});
$('sync-play').addEventListener('click',()=>{if(syncPlaying)return;if(!syncItems.length){notify('Najpierw przygotuj widok.');return;}syncBase=syncSeconds;syncStarted=performance.now();syncPlaying=true;tickSync();syncTimer=setInterval(tickSync,500);});
$('sync-pause').addEventListener('click',pauseSync);
$('ha-form').addEventListener('submit',async(event)=>{event.preventDefault();try{
 const form=event.target;await api('/api/settings/home-assistant','PATCH',{url:form.elements.url.value,token:form.elements.token.value,enabled:form.elements.enabled.checked});form.elements.token.value='';notify('Ustawienia Home Assistant zapisane.');
}catch(error){notify(error.message,'error');}});
$('storage-form').addEventListener('submit',async(event)=>{event.preventDefault();try{
  const value=formData(event.target);const minimum=Math.round(Number(value.minimum)*1e9),buffer=Math.round(Number(value.buffer)*1e9);
  if(!Number.isSafeInteger(minimum)||!Number.isSafeInteger(buffer)||minimum<0||buffer<0)throw new Error('Nieprawidłowe limity.');
  await api('/api/settings/storage','PATCH',{minimum_free:minimum,free_buffer:buffer});$('storage-limit-status').textContent='';notify('Limity miejsca zapisane.');
}catch(error){notify(error.message,'error');}});
$('archive-form').addEventListener('input',()=>{storageDirty=true;});
$('archive-form').addEventListener('submit',async(event)=>{event.preventDefault();
  if(storageRequest||storagePolling||storageCurrent?.change_pending)return;
  if(!window.confirm('Zapisać katalogi? Poprzednie nagrania pozostaną na starej ścieżce. Przy zmianie nagrywanie zostanie chwilowo wstrzymane.'))return;
  const form=event.target,archive=form.elements.archive.value.trim(),exports=form.elements.exports.value.trim(),stage=form.elements.staging.value.trim();
  // A nonempty field is always an explicit choice, even when the archive changes.
  const staging=stage||null;
  const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
  storageDirty=true;storagePolling=true;storageLocalMessage=null;
  try{const result=await api('/api/settings/storage/archive','POST',{archive,exports,...(staging?{staging}:{}),request_id:id});
    if(result.unchanged){storageDirty=false;const current=await api('/api/settings/storage');storageLocalMessage='Katalogi są już ustawione. Nie wprowadzono zmian.';renderStorage(current);}
    else {storageRequest=id;for(const name of ['archive','exports','staging'])form.elements[name].readOnly=true;$('storage-change-status').textContent='Zmiana katalogów oczekuje na przetworzenie.';}
  }catch(error){storageLocalMessage=error.message;$('storage-change-status').textContent=error.message;notify(error.message,'error');}finally{storagePolling=false;}
});
setInterval(pollStorage,3000);
let indexPollPending=false,indexActionPending=false,indexEpoch=0;
function renderIndexState(value){
  const labels={idle:'Oczekiwanie',queued:'W kolejce',running:'Indeksowanie w toku',paused:'Wstrzymane',done:'Zakończono',error:'Błąd indeksowania'};
  $('archive-index-status').textContent=(labels[value.state]||'Brak danych')+(value.error?' ('+value.error+')':'');
  $('archive-index-progress').hidden=!['queued','running'].includes(value.state);
  $('archive-index-run').disabled=indexActionPending||['queued','running'].includes(value.state);
  $('archive-index-pause').disabled=indexActionPending||!['running','queued'].includes(value.state);
  const n=x=>Number(x||0).toLocaleString('pl-PL');
  $('archive-index-counts').textContent='Sprawdzono: '+n(value.scanned)+' | Zapisano / zweryfikowano: '+n(value.accepted)+' | Bez zmian: '+n(value.unchanged)+' | Pominięto (otwarte): '+n(value.skipped)+' | Usunięto pustych: '+n(value.empty_removed)+' | Puste odroczone: '+n(value.empty_deferred)+' | Błędy: '+n(value.failed)+(value.state==='running'&&value.started?' | Średnio: '+n(Math.round((value.scanned||0)/Math.max(1,Date.now()/1000-value.started)))+' plików/s':'');
}
async function pollIndex(){
  if(indexPollPending||indexActionPending||!me||me.role!=='admin'||document.hidden||$('admin-view').hidden||$('archive-index-panel').closest('[data-admin-section]')?.hidden)return;
  indexPollPending=true;const epoch=indexEpoch;
  try{
    const select=$('archive-index-camera'),selected=select.value;
    const options=cameras.map(c=>[c.slug,c.name]).filter(c=>c[0]);
    if(select.options.length!==options.length+1||options.some((c,i)=>select.options[i+1]?.value!==c[0]||select.options[i+1]?.textContent!==c[1])){
      select.replaceChildren(new Option('Wszystkie kamery',''),...options.map(c=>new Option(c[1],c[0])));select.value=selected;
    }
    const value=await api('/api/settings/index');if(epoch===indexEpoch&&!indexActionPending)renderIndexState(value);
  }catch(error){/* storage updates can briefly interrupt polling */}finally{indexPollPending=false;}
}
$('archive-index-run').addEventListener('click',async()=>{
  if(indexActionPending||$('archive-index-run').disabled)return;
  const start=$('archive-index-start').value||null,end=$('archive-index-end').value||null;
  if(Boolean(start)!==Boolean(end)){notify('Podaj obie daty indeksowania lub pozostaw obie puste.','error');return;}
  if(start&&end&&start>end){notify('Data początkowa nie może być późniejsza niż końcowa.','error');return;}
  await submitIndexAction({action:'start',camera:$('archive-index-camera').value||null,start,end});
});
$('archive-index-pause').addEventListener('click',async()=>{
  await submitIndexAction({action:'pause'});
});
async function submitIndexAction(data){
  if(indexActionPending)return;
  indexActionPending=true;indexEpoch++;
  $('archive-index-run').disabled=true;$('archive-index-pause').disabled=true;
  $('archive-index-status').textContent=data.action==='start'?'Przyjmowanie zlecenia…':'Wstrzymywanie…';
  try{
    const value=await api('/api/settings/index','POST',data);
    indexActionPending=false;renderIndexState(value);
  }catch(error){
    indexActionPending=false;$('archive-index-status').textContent='Nie potwierdzono zlecenia - sprawdzanie stanu';
    $('archive-index-run').disabled=false;$('archive-index-pause').disabled=false;
    notify(error.message,'error');await pollIndex();
  }finally{indexActionPending=false;}
}
setInterval(pollIndex,3000);

$('recording-segment-save').addEventListener('click',async()=>{
  const seconds=Number($('recording-segment').value);
  if(!window.confirm('Zmienić długość nowych segmentów RTSP? Procesy zapisu zostaną na krótko ponownie uruchomione.'))return;
  try{await api('/api/settings/recording','PATCH',{segment_seconds:seconds});$('recording-segment-status').textContent='Zapisano. Ustawienie zostanie zastosowane do nowych segmentów RTSP.';}catch(error){$('recording-segment-status').textContent=error.message;}
});
$('protect-range').addEventListener('click',async()=>{
  if(protectionSubmitting)return;
  const camera=activeCamera,generation=exportViewGeneration;protectionSubmitting=true;
  const value=formData($('export-form'));
  try{
    if(!value.start||!value.end)throw new Error('Wybierz początek i koniec zakresu.');
    const request={start_local:value.start,end_local:value.end,start_fold:value.start_fold===''?null:Number(value.start_fold),end_fold:value.end_fold===''?null:Number(value.end_fold),gaps:value.gaps};
    const result=await api(`/api/cameras/${camera.id}/export-protect`,'POST',request);
    if(generation!==exportViewGeneration||camera!==activeCamera)return;
    await loadArchive();if(generation===exportViewGeneration&&camera===activeCamera)notify(`Ochroną objęto pliki: ${result.files}. Nowo zabezpieczone: ${result.protected}.`);
  }catch(error){if(generation===exportViewGeneration&&camera===activeCamera)notify(error.message,'error');}
  finally{if(generation===exportViewGeneration)protectionSubmitting=false;}
});
$('export-form').addEventListener('submit',async(event)=>{
  event.preventDefault();if(exportSubmitting)return;
  const camera=activeCamera,generation=exportViewGeneration;exportSubmitting=true;const value=formData(event.target);
  try{
    if(!value.start||!value.end)throw new Error('Wybierz zakres czasu.');
    const request={start_local:value.start,end_local:value.end,start_fold:value.start_fold===''?null:Number(value.start_fold),end_fold:value.end_fold===''?null:Number(value.end_fold),gaps:value.gaps};
    const preview=await api(`/api/cameras/${camera.id}/export-preview`,'POST',request);
    if(generation!==exportViewGeneration||camera!==activeCamera||$('archive-view').hidden)return;
    if(preview.gaps.length && !window.confirm(`W zaznaczonym czasie są ${preview.gaps.length} przerwy o łącznym czasie ${Math.round(preview.missing_seconds)} s. Czas wynikowego pliku: ${Math.round(preview.output_seconds)} s. Przygotować plik?`))return;
    const job=await api(`/api/cameras/${camera.id}/export-jobs`,'POST',request);
    if(generation!==exportViewGeneration||camera!==activeCamera){await refreshExports();return;}
    const box=$('export-result');box.replaceChildren(element('p','Eksport w kolejce.'));await refreshExports();
    if(generation!==exportViewGeneration||camera!==activeCamera)return;
    if(exportTimer!==null)clearInterval(exportTimer);let polling=false;
    const timer=setInterval(async()=>{
      if(polling)return;polling=true;
      try{const state=await api(`/api/export-jobs/${job.id}`);if(generation!==exportViewGeneration||camera!==activeCamera||exportTimer!==timer)return;
        const labels={queued:'W kolejce',running:'Przetwarzanie',done:'Gotowe',failed:'Błąd'};const progress=state.status==='running'?' - '+Math.round(Math.max(0,Math.min(1,state.progress))*100)+'%':'';box.replaceChildren(element('p','Status: '+(state.cancel_requested?'Anulowanie':state.cancelled?'Anulowano':labels[state.status]||state.status)+progress));
        if(state.status==='done'){clearInterval(timer);if(exportTimer===timer)exportTimer=null;refreshExports();const link=element('a','Pobierz gotowy MP4');link.href=`/api/export-jobs/${job.id}/file`;box.append(link);}
        if(state.status==='failed'){clearInterval(timer);if(exportTimer===timer)exportTimer=null;refreshExports();box.append(element('p',state.error||'Eksport nie powiódł się.'));}
      }catch(error){clearInterval(timer);if(exportTimer===timer){exportTimer=null;if(generation===exportViewGeneration&&camera===activeCamera)notify(error.message,'error');}}
      finally{polling=false;}
    },3000);
    exportTimer=timer;
  }catch(error){if(generation===exportViewGeneration&&camera===activeCamera)notify(error.message,'error');}
  finally{if(generation===exportViewGeneration)exportSubmitting=false;}
});
setInterval(async()=>{if(!me||document.hidden)return;try{const fresh=(await api('/api/cameras')).cameras;for(const camera of fresh){const card=document.querySelector(`.camera[data-camera-id="${camera.id}"]`);const badge=card&&card.querySelector('.health');if(badge)badge.textContent=cameraHealthText(camera);}}catch(error){notify(error.message,'error');}},60000);
if(typeof window.addEventListener==='function'){window.addEventListener('pagehide',()=>stopTalkback());document.addEventListener('visibilitychange',()=>{if(document.hidden)stopTalkback();});}
start();
