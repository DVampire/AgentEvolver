/* Shared standalone Usage component. No model calls, external scripts or global IDs. */
const palette = ['#5be1b1', '#75adff', '#b5a1ff', '#f3be6d', '#8a9da0'];
const TOKEN_LINES = [
  {key:'input_tokens',label:'Input (uncached)',color:'#5be1b1',help:'Uncached input only; excludes cache reads and writes.'},
  {key:'output_tokens',label:'Output',color:'#f3be6d',help:'Output tokens; reasoning is a subset when reported.'},
  {key:'cache_tokens',label:'Cache',color:'#75adff',help:'Cache reads + cache writes. Not an extra copy of full input.'},
  {key:'total_tokens',label:'Total',color:'#ecf8f2',dash:'6 4',help:'Full input + output; includes cached input exactly once.'},
  {key:'cache_read_tokens',label:'Cache read',color:'#3bd1dc',help:'Input tokens read from cache; a subset of Cache.'},
  {key:'cache_write_tokens',label:'Cache write',color:'#b5a1ff',help:'Input tokens written to cache; a subset of Cache.'},
  {key:'context_input_tokens',label:'Full input',color:'#f49ac7',dash:'3 3',help:'Input including cached and unclassified input; overlaps Input and Cache.'},
  {key:'unclassified_input',label:'Unclassified input',color:'#8a9da0',help:'Recorded full input not covered by its reported input components.'},
  {key:'reasoning_tokens',label:'Reasoning',color:'#f98974',dash:'2 3',help:'Reported reasoning tokens within output; not added to Total.'},
];
const TOKEN_PRESETS = {
  overview:['input_tokens','output_tokens','cache_tokens','total_tokens'],
  cache:['input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','total_tokens'],
  all:TOKEN_LINES.map(d=>d.key), none:[],
};
const numeric = v => v == null ? '—' : Number(v).toLocaleString('en-US', {maximumFractionDigits: 0});
const money = v => v == null ? 'Not reported' : '$' + Number(v).toFixed(4);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const when = value => value ? new Date(value).toLocaleString() : 'Time not recorded';

export function mountUsage(root, options = {}) {
  root.classList.add('ae-usage');
  if (options.layout === 'compact') root.classList.add('ae-usage-compact');
  const endpoint = options.endpoint || './api/usage';
  let filters = {metric: 'cost', axis: 'call', bucket: '60', group_by: 'agent_name', page: 0, limit: 25, ...options.initialFilters};
  let kind = 'line', data, timer, controller, destroyed = false, focusBefore;
  let tokenBarLayout='grouped';
  let selectedTokens=new Set((options.initialTokenSeries ?? TOKEN_PRESETS.overview).filter(k=>TOKEN_LINES.some(d=>d.key===k)));
  const tokenChoice = d => `<label class="u-token-choice" title="${d.help}"><input type="checkbox" data-token-series="${d.key}" ${selectedTokens.has(d.key)?'checked':''}><i></i><span>${d.label}</span></label>`;
  root.innerHTML = `
    <div class="u-heading"><div><p class="u-eyebrow">Telemetry · usage explorer</p><h2>Usage & cost</h2><p class="u-subtitle">Real trace records · filter, compare and inspect individual calls.</p></div><span class="u-state" role="status">Loading usage…</span></div>
    <div class="u-filters">
      <label>Time range<select data-filter="range"><option value="all">Entire history</option><option value="last15">Last 15 min of recorded activity</option></select></label>
      <label>Agent<select data-filter="agent_name"><option value="">All agents</option></select></label>
      <label>Model<select data-filter="model"><option value="">All models</option></select></label>
      <label>Operation<select data-filter="operation"><option value="">All operations</option></select></label>
      <label>Cost source<select data-filter="cost_source"><option value="">All sources</option><option>reported</option><option>estimated</option><option>legacy</option><option>unknown</option></select></label>
      <button class="u-clear" type="button">Reset filters</button><button class="u-export" type="button">Export CSV ↓</button>
    </div>
    <div class="u-cards"></div>
    <div class="u-chart-panel">
      <div class="u-chart-head"><h3 class="u-chart-title">Cost per completed step</h3><div class="u-segments u-metrics">${['cost','tokens','calls','latency'].map((v, i) => `<button type="button" data-metric="${v}" aria-pressed="${i===0}">${['Cost','Tokens','Calls','Step duration'][i]}</button>`).join('')}</div></div>
      <div class="u-tools">
        <div class="u-segments u-axis"><button type="button" data-axis="call" aria-pressed="true">Per recorded call</button><button type="button" data-axis="time" aria-pressed="false">By time</button></div>
        <div class="u-segments u-kind"><button type="button" data-kind="line" aria-pressed="true">Line</button><button type="button" data-kind="bar" aria-pressed="false">Bar</button></div>
        <label>Bucket<select data-filter="bucket"><option value="5">5 sec</option><option value="60" selected>1 min</option><option value="300">5 min</option><option value="3600">1 hour</option><option value="86400">1 day</option></select></label>
        <label><input class="u-cumulative" type="checkbox">Cumulative</label><div class="u-legend"></div>
      </div>
      <div class="u-token-controls" hidden>
        <div class="u-token-primary" role="group" aria-label="Visible token series">${TOKEN_LINES.slice(0,4).map(tokenChoice).join('')}</div>
        <details class="u-token-more"><summary>More token series</summary><div class="u-token-secondary" role="group" aria-label="Detailed token series">${TOKEN_LINES.slice(4).map(tokenChoice).join('')}</div></details>
        <div class="u-token-presets"><span>Presets</span><button type="button" data-token-preset="overview">Overview</button><button type="button" data-token-preset="cache">Cache detail</button><button type="button" data-token-preset="all">All</button><button type="button" data-token-preset="none">None</button><label class="u-token-bar-layout" hidden>Bars <select class="u-token-bar-select"><option value="grouped">Grouped comparison</option><option value="stacked">Stacked components + reference lines</option></select></label></div>
        <p class="u-token-note"></p>
      </div>
      <div class="u-chart-wrap"><svg class="u-chart" viewBox="0 0 1200 300" role="img" aria-label="Usage chart; corresponding records are available in the table below"></svg><div class="u-tooltip" hidden></div><p class="u-empty" hidden>No timed records for these filters. Historical summaries remain below.</p></div>
      <p class="u-coverage"></p>
    </div>
    <div class="u-bottom"><section class="u-ranking"><div class="u-chart-head"><h3>Cost attribution</h3><select data-filter="group_by"><option value="agent_name">By agent</option><option value="model">By model</option><option value="provider">By provider</option><option value="benchmark_task_id">By benchmark task</option></select></div><div class="u-breakdown"></div></section>
    <section class="u-table-section"><div class="u-chart-head"><h3>Call records</h3><select data-filter="sort"><option value="time">Newest first</option><option value="cost">Highest cost</option></select></div><div class="u-table-wrap"><table><thead><tr><th>Time / step</th><th>Agent / model</th><th>Full input</th><th>Output</th><th>USD / source</th></tr></thead><tbody></tbody></table></div><div class="u-pages"><button class="u-prev" type="button">← Previous</button><span></span><button class="u-next" type="button">Next →</button></div></section></div>
    <p class="u-history"></p>
    <p class="u-note">Legacy traces record completed Agent steps. Model retries may be included in a step; step duration includes tools and is not model latency. Unknown usage is not zero. Provider-reported and estimated amounts are not account invoices.</p>
    <aside class="u-drawer" role="dialog" aria-modal="false" aria-label="Usage record details" hidden><button class="u-close" type="button">Close ×</button><h3>Call details</h3><dl></dl></aside>`;
  const find = selector => root.querySelector(selector);
  const url = extra => { const u = new URL(endpoint, location.href); Object.entries({...filters, ...extra}).forEach(([k,v]) => { if (v !== '' && v != null) u.searchParams.set(k, v); }); return u; };
  function setPressed(selector, key, value) { root.querySelectorAll(selector).forEach(b => b.setAttribute('aria-pressed', String(b.dataset[key] === value))); }
  function cards(summary) {
    const ratio = summary.cache_hit_ratio;
    const fields = [
      ['Known cost · USD', summary.costed_records ? money(summary.cost) : 'Not reported', `${numeric(summary.costed_calls)} / ${numeric(summary.calls)} calls have cost`],
      ['Total tokens', summary.token_calls ? numeric(summary.total_tokens) : 'Not reported', `Full input + output · ${numeric(summary.token_calls)} calls with totals`],
      ['Recorded calls', numeric(summary.calls), `${numeric(summary.request_attempts)} matched request starts in traces`],
      ['Cache hit ratio', ratio == null ? '—' : (ratio * 100).toFixed(1) + '%', 'Cache reads / full input · weighted by tokens']
    ];
    find('.u-cards').replaceChildren(...fields.map(([label,value,note]) => { const c=el('article',null,'u-card'); c.append(el('span',label),el('strong',value),el('small',note)); return c; }));
  }
  function facets(f) {
    for (const field of ['agent_name','model','operation']) {
      const select = find(`[data-filter="${field}"]`);
      const first = el('option', {model:'All models',agent_name:'All agents',operation:'All operations'}[field]); first.value='';
      select.replaceChildren(first, ...(f[field]||[]).map(v=>{const o=el('option',v);o.value=v;return o;}));
      select.value=filters[field]||'';
    }
  }
  function svg(tag, attrs, parent=find('.u-chart')) { const e=document.createElementNS('http://www.w3.org/2000/svg',tag); Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));parent.append(e);return e; }
  function text(x,y,value,attrs={}) { const e=svg('text',{x,y,fill:'#96b5a7','font-size':11,...attrs});e.textContent=value;return e; }
  async function detail(id) {
    try {
      const response=await fetch(url({view:'call',id}),{cache:'no-store'}); if(!response.ok) throw Error(`HTTP ${response.status}`);
      const r=await response.json(); if(destroyed)return;
      focusBefore=document.activeElement;
      const fields={
        'Record ID':r.id,'Granularity':r.granularity,'Timestamp':when(r.timestamp),'Agent':r.agent_name,'Process':r.task_id,
        'Model':r.model,'Provider':r.provider,'Operation':r.operation,'Benchmark task':r.benchmark_task_id,'Attempt':r.attempt_id,
        'Step':r.step_number==null?null:r.step_number+1,'Matched request starts':r.request_attempts,
        'Uncached input':r.input_tokens,'Cache reads':r.cache_read_tokens,'Cache writes':r.cache_write_tokens,
        'Full input':r.context_input_tokens,'Output':r.output_tokens,'Reasoning (subset of output)':r.reasoning_tokens,
        'Total tokens':r.total_tokens,'Cost USD':r.cost,'Cost source':r.cost_source,
        'Step duration (includes tools)':r.step_duration_ms==null?null:(r.step_duration_ms/1000).toFixed(2)+' s',
        'Model-only latency':null};
      find('.u-drawer dl').replaceChildren(...Object.entries(fields).flatMap(([k,v])=>[el('dt',k),el('dd',v==null?'Not recorded':String(v))]));
      find('.u-drawer').hidden=false;find('.u-close').focus();options.onSelectCall?.(r);
    }catch(e){find('.u-state').textContent='Cannot open record: '+e.message;}
  }
  function chart(series) {
    const metric = filters.metric;
    const tokenMode = metric === 'tokens';
    const visible = TOKEN_LINES.filter(line => selectedTokens.has(line.key));
    const stacked = tokenMode && kind === 'bar' && tokenBarLayout === 'stacked';
    find('.u-token-controls').hidden = !tokenMode;
    find('.u-token-bar-layout').hidden = !(tokenMode && kind === 'bar');
    find('.u-legend').hidden = tokenMode;
    find('.u-token-controls').querySelectorAll('[data-token-series]').forEach(input => {
      input.checked = selectedTokens.has(input.dataset.tokenSeries);
    });
    const ordinary = {key:'value', label:metric==='cost'?'Known USD':metric==='latency'?'Step duration · seconds':'Recorded calls', color:palette[0]};
    const definitions = tokenMode ? visible : [ordinary];
    const value = (point, definition) => tokenMode ? point.tokens?.[definition.key] ?? null : point.value;
    const isKnown = n => typeof n === 'number' && Number.isFinite(n);
    find('.u-legend').replaceChildren(...(tokenMode ? [] : [ordinary]).map(def => {
      const label=el('span'), dot=el('i');dot.style.background=def.color;label.append(dot,el('span',def.label));return label;
    }));
    find('.u-chart-title').textContent = {cost:'Cost per completed step',tokens:'Token consumption',calls:'Recorded call volume',latency:'Step duration · includes tools'}[metric] + (filters.cumulative==='true'&&metric!=='latency'?' · cumulative known values':'');
    find('.u-token-note').textContent = 'Input means uncached input. Cache = cache reads + writes. Total = full input + output; caches are counted once. Hiding a series does not change totals.' + (stacked ? ' Stacks contain selected disjoint components; overlapping aggregates and reasoning appear as lines.' : '') + ' Gaps mean unreported values; partial groups show known subtotals.';
    const canvas = find('.u-chart');canvas.replaceChildren();find('.u-tooltip').hidden=true;
    const empty = !series.length || !definitions.length;
    find('.u-empty').hidden = !empty;canvas.toggleAttribute('hidden',empty);
    find('.u-empty').textContent = tokenMode&&!visible.length ? 'No token series selected. Select a series above; summary totals are unchanged.' : 'No timed records for these filters. Historical summaries remain below.';
    if (empty) return;
    // Aggregate cache overlaps its read/write children; totals, full input and
    // reasoning always remain reference lines, never additional stacked tokens.
    const stackKeys = new Set(['input_tokens','output_tokens','unclassified_input',
      ...(selectedTokens.has('cache_tokens') ? ['cache_tokens'] : ['cache_read_tokens','cache_write_tokens'])]);
    const stackDefinitions = stacked ? visible.filter(d => stackKeys.has(d.key)) : [];
    const lineDefinitions = kind==='line' ? definitions : stacked ? visible.filter(d=>!stackKeys.has(d.key)) : [];
    const stackValues = series.map(p => {
      const values=stackDefinitions.map(d=>value(p,d));
      return values.length && values.every(isKnown) && !p.token_conflicts ? values.reduce((a,b)=>a+b,0) : null;
    });
    const W=1200,H=300,L=75,R=18,T=20,B=42,h=H-T-B,inner=W-L-R;
    const knownValues=series.flatMap(p=>definitions.map(d=>value(p,d))).filter(isKnown);
    const max=Math.max(...knownValues,...stackValues.filter(isKnown),metric==='cost'?.001:1)*1.12;
    const x=i=>L+(i+.5)*inner/series.length, y=v=>T+h-v/max*h;
    canvas.dataset.yMax=String(max);
    for(let i=0;i<5;i++){
      const yy=T+h-i*h/4;
      svg('line',{x1:L,x2:W-R,y1:yy,y2:yy,stroke:'#29463d','stroke-dasharray':i?'3 5':'none'});
      text(L-10,yy+4,metric==='cost'?'$'+(max*i/4).toFixed(max<.1?4:2):numeric(max*i/4),{'text-anchor':'end'});
    }
    function drawLine(definition) {
      const group=svg('g',{'data-drawn-series':definition.key,'data-series-style':'line'});
      let segment=[];
      const draw=()=>{
        if(segment.length)svg('polyline',{points:segment.join(' '),fill:'none',stroke:definition.color,
          'stroke-width':definition.key==='total_tokens'?3:2,'stroke-dasharray':definition.dash||'none'},group);
        segment=[];
      };
      series.forEach((point,i)=>{
        const n=value(point,definition);
        if(!isKnown(n)){draw();return;}
        segment.push(x(i)+','+y(n));
        if(series.length<=150)svg('circle',{cx:x(i),cy:y(n),r:3,fill:definition.color},group);
      });
      draw();
    }
    if(kind==='bar'){
      const width=Math.max(.5,Math.min(52,inner/series.length*.8));
      series.forEach((point,i)=>{
        if(stacked){
          if(!isKnown(stackValues[i]))return;
          const group=svg('g',{'data-stack-point':i,'data-stack-total':stackValues[i]});let height=0;
          stackDefinitions.forEach(definition=>{
            const n=value(point,definition);
            svg('rect',{x:x(i)-width/2,y:y(height+n),width,height:n/max*h,fill:definition.color,
              'data-drawn-series':definition.key,'data-series-style':'stacked'},group);height+=n;
          });
          if(stackDefinitions.some(d=>(point.token_coverage?.[d.key]??0)<(point.token_observations??point.count)))
            svg('line',{x1:x(i)-width/2,x2:x(i)+width/2,y1:y(height),y2:y(height),stroke:'#f3be6d','stroke-dasharray':'2 2'},group);
        }else{
          const barWidth=width/definitions.length;
          definitions.forEach((definition,j)=>{
            const n=value(point,definition);if(!isKnown(n))return;
            svg('rect',{x:x(i)-width/2+j*barWidth,y:y(n),width:Math.max(.2,barWidth*.86),height:n/max*h,
              fill:definition.color,'data-drawn-series':definition.key,'data-series-style':'grouped'});
          });
        }
      });
    }
    lineDefinitions.forEach(drawLine);
    function hover(point) {
      const tooltip=find('.u-tooltip');
      const title=filters.axis==='time'?when(point.label):'#'+point.label+' · '+when(point.from);
      tooltip.replaceChildren(el('strong',title),el('p',point.count+' record(s)'+(filters.cumulative==='true'?' · cumulative known values':'')));
      if(point.operations?.length)tooltip.append(el('p','Operation: '+point.operations.join(', ')));
      definitions.forEach(definition=>{
        const n=value(point,definition);
        const row=el('div',null,'u-tip-row'),label=el('span',definition.label),amount=el('strong',!isKnown(n)?'Not reported':metric==='cost'?money(n):numeric(n));
        label.style.color=definition.color;row.append(label,amount);tooltip.append(row);
        if(tokenMode){
          const observed=point.token_observations??point.count,known=point.token_coverage?.[definition.key]??0;
          if(known<observed)tooltip.append(el('small',`${definition.label}: ${known}/${observed} records with this field`));
        }
      });
      if(tokenMode&&point.token_conflicts)tooltip.append(el('p','Reported input breakdown conflicts with full input; stacking is omitted.'));
      if(!tokenMode&&metric==='cost')tooltip.append(el('small',`Cost coverage ${point.costed}/${point.count}`));
      tooltip.hidden=false;
    }
    // One hit region per x position gives a shared tooltip for every visible
    // series, including fields whose value is missing at this position.
    series.forEach((point,i)=>{
      const hit=svg('rect',{x:L+i*inner/series.length,y:T,width:inner/series.length,height:h,
        fill:'transparent',tabindex:0,role:'button','data-point-index':i,
        'aria-label':(filters.axis==='time'?when(point.label):'Record '+point.label)+'; '+definitions.map(d=>d.label+': '+numeric(value(point,d))).join('; ')});
      hit.style.cursor='pointer';hit.onmouseenter=hit.onfocus=()=>hover(point);
      hit.onmouseleave=hit.onblur=()=>{find('.u-tooltip').hidden=true;};
      hit.onclick=()=>{if(point.id)detail(point.id);else{filters.from=point.from;filters.to=new Date(Date.parse(point.to)+1).toISOString();filters.page=0;refresh();}};
      hit.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();hit.onclick();}};
      if(i%Math.max(1,Math.ceil(series.length/8))===0||i===series.length-1)
        text(x(i),H-13,filters.axis==='time'?new Date(point.label).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'#'+point.label,{'text-anchor':'middle','pointer-events':'none'});
    });
  }
  function render(d) {
    setPressed('[data-metric]','metric',filters.metric);
    setPressed('[data-axis]','axis',filters.axis);
    find('[data-filter=bucket]').disabled=filters.axis!=='time';
    find('[data-filter=bucket]').title=filters.axis==='time'?'Time interval for aggregation':'Applies to By time only';
    find('.u-cumulative').checked=filters.cumulative==='true';
    find('.u-cumulative').disabled=filters.metric==='latency';
    data=d;root.dataset.loaded='true';cards(d.summary);facets(d.facets);chart(d.series);
    const s=d.summary;
    const batchSize=Math.max(1,...d.series.map(p=>p.count));
    const grouped=filters.axis==='call'&&batchSize>1?` Chart points group up to ${batchSize} adjacent records; click a point to inspect that range.`:'';
    find('.u-coverage').textContent=`Real trace data · ${numeric(d.coverage.requests||0)} request receipts · ${numeric(d.coverage.legacy_steps)} legacy step records · ${numeric(s.costed_calls)}/${numeric(s.calls)} calls with cost. Reported ${money(s.sources.reported)} · estimated ${money(s.sources.estimated)} · legacy source ${money(s.sources.legacy)}.${d.coverage.read_errors?' Some trace records could not be read.':''}${grouped}`;
    find('.u-breakdown').replaceChildren(...d.breakdown.slice(0,8).map((r,i)=>{
      const row=el('button',null,'u-rank');row.type='button';row.append(el('span',r.name),el('strong',money(r.cost)));
      const rail=el('span',null,'u-rail'),bar=el('i');bar.style.width=(Number(s.cost)?Number(r.cost)/Number(s.cost)*100:0)+'%';bar.style.background=palette[i%4];rail.append(bar);row.append(rail);
      row.onclick=()=>{filters[filters.group_by]=r.name;filters.page=0;refresh();};return row;
    }));
    find('tbody').replaceChildren(...d.calls.map(r=>{
      const row=el('tr');const name=r.granularity==='summary'?'Historical summary':when(r.timestamp)+(r.step_number==null?'':' · step '+(r.step_number+1));
      const cells=[name,(r.agent_name||'Unknown')+' / '+r.model,numeric(r.context_input_tokens),numeric(r.output_tokens),money(r.cost)+' · '+r.cost_source];
      cells.forEach((v,i)=>{const td=el('td');if(!i){const b=el('button',v,'u-call');b.type='button';b.onclick=()=>detail(r.id);td.append(b);}else td.textContent=v;row.append(td);});return row;
    }));
    find('.u-pages span').textContent=`${d.row_count?d.page*d.limit+1:0}–${Math.min((d.page+1)*d.limit,d.row_count)} / ${numeric(d.row_count)} records`;
    find('.u-prev').disabled=d.page===0;find('.u-next').disabled=(d.page+1)*d.limit>=d.row_count;
    find('.u-history').textContent=d.coverage.summary_rows?`${numeric(d.historical.calls)} calls (${money(d.historical.cost)} known cost) are available only as historical summaries. They are included in totals, not invented as timed points.`:'';
    find('.u-state').textContent='Updated '+new Date(d.updated_at).toLocaleTimeString();
  }
  async function refresh() {
    if(destroyed)return;clearTimeout(timer);controller?.abort();const current=new AbortController();controller=current;
    try { const response=await fetch(url({view:'overview'}),{cache:'no-store',signal:current.signal});if(!response.ok)throw Error(`HTTP ${response.status}`);const d=await response.json();if(!destroyed&&controller===current)render(d); }
    catch(e){if(e.name!=='AbortError')find('.u-state').textContent='Last view retained · '+e.message;}
    finally{if(!destroyed&&controller===current)timer=setTimeout(refresh,document.hidden?30000:5000);}
  }
  function selectTokens(keys) {
    selectedTokens=new Set(keys.filter(k=>TOKEN_LINES.some(d=>d.key===k)));
    if(data)chart(data.series);
  }
  root.querySelectorAll('[data-token-series]').forEach(input=>{
    // Set individual style properties, as for chart legends, under the Run
    // dashboard's CSP; literal HTML style attributes are intentionally blocked.
    input.nextElementSibling.style.backgroundColor=TOKEN_LINES.find(d=>d.key===input.dataset.tokenSeries).color;
    input.onchange=()=>{
      if(input.checked)selectedTokens.add(input.dataset.tokenSeries);else selectedTokens.delete(input.dataset.tokenSeries);
      if(data)chart(data.series);
    };
  });
  root.querySelectorAll('[data-token-preset]').forEach(button=>button.onclick=()=>selectTokens(TOKEN_PRESETS[button.dataset.tokenPreset]));
  find('.u-token-bar-select').onchange=e=>{tokenBarLayout=e.target.value;if(data)chart(data.series);};
  root.querySelectorAll('[data-filter]').forEach(select=>select.onchange=()=>{filters[select.dataset.filter]=select.value;filters.page=0;refresh();});
  root.querySelectorAll('[data-metric]').forEach(b=>b.onclick=()=>{filters.metric=b.dataset.metric;setPressed('[data-metric]','metric',filters.metric);find('.u-cumulative').disabled=filters.metric==='latency';refresh();});
  root.querySelectorAll('[data-axis]').forEach(b=>b.onclick=()=>{filters.axis=b.dataset.axis;setPressed('[data-axis]','axis',filters.axis);refresh();});
  root.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;setPressed('[data-kind]','kind',kind);if(data)chart(data.series);});
  find('.u-cumulative').onchange=e=>{filters.cumulative=String(e.target.checked);refresh();};
  find('.u-prev').onclick=()=>{filters.page--;refresh();};find('.u-next').onclick=()=>{filters.page++;refresh();};
  find('.u-clear').onclick=()=>{for(const k of ['from','to','agent_name','model','provider','benchmark_task_id','cost_source','operation'])delete filters[k];filters.range='all';filters.page=0;find('[data-filter="range"]').value='all';find('[data-filter="cost_source"]').value='';refresh();};
  find('.u-export').onclick=()=>{const a=el('a');a.href=url({view:'export'});a.download='usage.csv';a.click();};
  const close=()=>{find('.u-drawer').hidden=true;focusBefore?.focus();};find('.u-close').onclick=close;
  const keyboard=e=>{if(e.key==='Escape'&&!find('.u-drawer').hidden)close();};root.addEventListener('keydown',keyboard);
  refresh();
  return {refresh,setTokenSeries:selectTokens,getTokenSeries(){return [...selectedTokens];},setFilters(next){filters={...filters,...next,page:0};refresh();},destroy(){destroyed=true;clearTimeout(timer);controller?.abort();root.removeEventListener('keydown',keyboard);root.replaceChildren();}};
}
for(const root of document.querySelectorAll('[data-usage-endpoint]')) mountUsage(root,{endpoint:root.dataset.usageEndpoint});
