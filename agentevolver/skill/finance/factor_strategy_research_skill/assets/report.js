"use strict";
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
const colors = ["--mint", "--blue", "--amber", "--purple", "--red"];
function number(v, unit) { return v == null ? "Unavailable" : (unit === "percent" ? (v * 100).toFixed(2) + "%" : Number(v).toLocaleString(undefined, {maximumFractionDigits: 4})); }
function table(headers, rows) {
  const wrap = node("div", null, "table-wrap"), t = node("table"), head = node("thead"), tr = node("tr");
  headers.forEach(h => tr.append(node("th", h))); head.append(tr); t.append(head);
  const body = node("tbody"); rows.forEach(row => { const r = node("tr"); row.forEach(v => r.append(node("td", v))); body.append(r); }); t.append(body); wrap.append(t); return wrap;
}
function chart(c) {
  const fig = node("figure"); fig.dataset.chartId = c.id;
  fig.append(node("h3", c.title), node("figcaption", `${c.split} · ${c.x_label} / ${c.y_label}`));
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  const canvasWidth=Math.min(620,Math.max(280,document.querySelector(".shell").clientWidth-42)),plotWidth=canvasWidth-90;
  svg.setAttribute("viewBox", `0 0 ${canvasWidth} 300`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `${c.title}: ${c.y_label} by ${c.x_label}`);
  const el = (tag, attrs, text) => {const n = document.createElementNS(ns,tag); Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v)); if(text != null)n.textContent=text; svg.append(n); return n;};
  const xs = [...new Set(c.series.flatMap(s=>s.points.map(p=>p.x)))]; if(c.kind === "line")xs.sort((a,b)=>typeof a==="number" ? a-b : String(a).localeCompare(String(b)));
  const ys = c.series.flatMap(s=>s.points.filter(p=>p.y!=null).map(p=>p.y));
  let low = Math.min(...ys), high = Math.max(...ys); if(c.kind==="bar"){low=Math.min(0,low);high=Math.max(0,high);} if(low===high){low-=1;high+=1;}
  const pad=(high-low)*.08; low-=pad;high+=pad;
  const numericX = xs.every(x=>typeof x==="number");
  const dateX = xs.every(x=>typeof x==="string" && /^\d{4}-\d{2}-\d{2}$/.test(x));
  const coordinate = x=>numericX ? x : dateX ? Date.parse(x+"T00:00:00Z") : xs.indexOf(x);
  const firstX=coordinate(xs[0]),lastX=coordinate(xs[xs.length-1]);
  const X=x=>c.kind==="line" && lastX>firstX ? 70+(coordinate(x)-firstX)/(lastX-firstX)*plotWidth : 70+(xs.indexOf(x)+.5)*plotWidth/xs.length, Y=y=>248-(y-low)/(high-low)*200;
  for(let i=0;i<5;i++){const v=low+(high-low)*i/4;el("line",{x1:66,x2:canvasWidth-18,y1:Y(v),y2:Y(v),stroke:"var(--border)"});el("text",{x:59,y:Y(v)+4,"text-anchor":"end"},Number(v.toPrecision(4)));}
  if(low<=0&&high>=0)el("line",{x1:66,x2:canvasWidth-18,y1:Y(0),y2:Y(0),stroke:"var(--muted)","stroke-dasharray":"4 4"});
  [...new Set([0,Math.floor((xs.length-1)/2),xs.length-1])].forEach(i=>el("text",{x:X(xs[i]),y:274,"text-anchor":i===0?"start":i===xs.length-1?"end":"middle"},String(xs[i])));
  const legend=node("div",null,"legend");
  c.series.forEach((s,i)=>{
    const color=`var(${colors[i%colors.length]})`, item=node("span",s.label), dot=node("i"); dot.style.background=color;item.prepend(dot);legend.append(item);
    if(c.kind==="line"){
      let active=false,d="";s.points.forEach(p=>{if(p.y==null){active=false;return;}d+=`${active?"L":"M"}${X(p.x)},${Y(p.y)} `;active=true;});
      el("path",{d,fill:"none",stroke:color,"stroke-width":2.5});
    }
    s.points.forEach(p=>{
      if(p.y==null)return;
      let mark;if(c.kind==="bar"){
        const width=Math.max(1,plotWidth/xs.length*.75/c.series.length), x=X(p.x)-width*c.series.length/2+i*width;
        mark=el("rect",{x,y:Math.min(Y(0),Y(p.y)),width,height:Math.abs(Y(p.y)-Y(0)),fill:color,rx:2});
      }else{mark=el("circle",{cx:X(p.x),cy:Y(p.y),r:2,fill:color});}
      const title=document.createElementNS(ns,"title");title.textContent=`${s.label} · ${p.x}: ${p.y}`;mark.append(title);
    });
  });
  fig.append(svg,legend);const details=node("details"), summary=node("summary","Underlying values");details.append(summary,table([c.x_label,"Series",c.y_label],c.series.flatMap(s=>s.points.map(p=>[p.x,s.label,p.y==null?"Unavailable":p.y]))));fig.append(details);return fig;
}
async function main(){
  const response=await fetch("analysis.json",{cache:"no-store"});if(!response.ok)throw Error(`Data request failed: ${response.status}`);const data=await response.json();
  document.getElementById("title").textContent=data.title;document.title=`${data.title} · Research`;
  document.getElementById("summary").textContent=data.summary;
  const notice=document.getElementById("notice");notice.textContent=data.scope==="synthetic"?"Engineering fixture — synthetic observations, no market-performance claim.":"Measured research results · saved numerical artifacts · self-evaluation";
  if(data.scope==="synthetic")notice.className="warning";
  const basis=document.getElementById("basis");basis.append(node("p",data.data_basis),node("p",`Strict data qualification: ${data.strict_data.status}`,data.strict_data.status==="met"?"pass":"warning"),node("p",data.strict_data.reasons.join(" · ")),node("p",`Final test: ${data.test_state}`));
  for(const kind of ["factors","strategies"]){const section=document.getElementById(kind), inventory=section.querySelector(".inventory");
    if(!data[kind].length)inventory.append(node("p","Pending — no results for this stage yet."));
    data[kind].forEach(c=>{const article=node("article",null,"candidate");article.id=`candidate-${c.id}`;article.append(node("h3",`${c.id} · ${c.name}`),node("p",c.status,"status"),node("p",c.definition,kind==="factors"?"formula":"rules"));
      if(c.factor_ids.length){const links=node("p","Factors: ");c.factor_ids.forEach(id=>{const a=node("a",id+" ");a.href=`#candidate-${id}`;links.append(a);});article.append(links);}
      article.append(table(["Metric","Split","Value","Definition / unit"],c.metrics.map(m=>[m.label,m.split,number(m.value,m.unit),`${m.definition} (${m.unit})${m.reason?" · "+m.reason:""}`])));if(c.reason)article.append(node("p",c.reason));inventory.append(article);
    });data.charts.filter(c=>c.section===kind).forEach(c=>section.querySelector(".charts").append(chart(c)));
  }
  const provenance=document.getElementById("provenance");Object.entries(data.sources).forEach(([id,hash])=>provenance.append(node("p",`${id} · SHA-256 ${hash}`)));
  let resizeTimer;window.addEventListener("resize",()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{
    for(const kind of ["factors","strategies"]){const target=document.querySelector(`#${kind} .charts`);target.replaceChildren(...data.charts.filter(c=>c.section===kind).map(chart));}
  },150);});
}
main().catch(error=>{const n=document.getElementById("notice");n.className="warning";n.textContent=`Report data could not load: ${error.message}. Reload after the data artifact is available.`;});
