const $ = (id) => document.getElementById(id);
const formatter = new Intl.NumberFormat('en-US');
const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
let regions = [];
let selectedMonth = null;
let dataset = [];
let selectedRegion = 'punjab';
let currentFilter = {};
let runtimeLimits={max_csv_upload_bytes:15_000_000,max_backfill_days:90,max_export_rows:100_000,max_vegetation_composites:8};
let anomalyData = null;
let focusedWeek = null;
let changeExplanation = null;

function sourceLabel(source) {
  const names={MODIS_SP:'MODIS · SP',MODIS_NRT:'MODIS · NRT',VIIRS_SNPP_SP:'VIIRS S-NPP · SP',VIIRS_SNPP_NRT:'VIIRS S-NPP · NRT',VIIRS_NOAA20_SP:'VIIRS NOAA-20 · SP',VIIRS_NOAA20_NRT:'VIIRS NOAA-20 · NRT',VIIRS_NOAA21_SP:'VIIRS NOAA-21 · SP',VIIRS_NOAA21_NRT:'VIIRS NOAA-21 · NRT'};
  return names[source]||source;
}
function viirsPlatformLabel(source) {
  const names={VIIRS_SNPP_SP:'VIIRS Suomi-NPP · SP',VIIRS_NOAA20_SP:'VIIRS NOAA-20 · SP',VIIRS_NOAA21_SP:'VIIRS NOAA-21 · SP'};
  return names[source]||'Selected VIIRS platform · SP';
}
function safe(text) {
  return String(text).replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
}
function showError(message) { const e=$('error');e.hidden=!message;e.textContent=message||''; }
function setImportMessage(message,isError=false) { const e=$('import-msg');e.textContent=message;e.style.color=isError?'#ffb09b':'#9fc9b6'; }
function importHeaders(contentType) { const h={}; if(contentType) h['content-type']=contentType; const token=$('admin-token')?.value?.trim(); if(token) h['X-EmberLens-Admin']=token; return h; }
async function api(path, options) {
  const res=await fetch(path,options);
  if (!res.ok) {
    let message=`HTTP ${res.status}`;
    try { const body=await res.json(); message=typeof body.detail==='string'?body.detail:JSON.stringify(body.detail); } catch {}
    throw new Error(message);
  }
  return res.json();
}
function dates() {
  const start=$('start').value, end=$('end').value;
  if (!start || !end || start>end) throw new Error('Choose a valid start and end date.');
  const region=$('region').value, sensor=$('sensor').value;
  return {region,start,end,sensor};
}
function urlParams(x) { return new URLSearchParams(x).toString(); }
function setLoading(b) { $('apply').disabled=b;$('apply').textContent=b?'Loading...':'Apply filters'; }

async function refresh() {
  try {
    showError(''); setLoading(true); selectedMonth=null;
    currentFilter=dates(); selectedRegion=currentFilter.region;
    syncChangePeriods();
    const params=urlParams(currentFilter);
    $('daily-readings').innerHTML='<div class="daily-empty">Loading daily sensor readings...</div>';
    const [summary, calendar, daily, points, status, coverage, profile]=await Promise.all([
      api('/api/summary?'+params), api('/api/calendar?'+params), api('/api/daily-readings?'+params),
      api('/api/points?'+params+'&limit=2000'),
      api('/api/status'), api('/api/coverage?'+urlParams({region:currentFilter.region,start:currentFilter.start,end:currentFilter.end})),
      api('/api/profile')
    ]);
    dataset=calendar.rows;
    renderSummary(summary);
    renderCalendar(calendar,coverage);
    renderDailyReadings(daily);
    renderCoverage(coverage);
    renderMap(points.points,points.total,points.truncated);
    renderComparison(summary);
    renderStatus(status);
    renderProfile(profile);
    // Stage 3 is intentionally independent; a scientific analysis error cannot
    // make the basic Stage 1/2 dashboard unavailable.
    await Promise.all([refreshHarmonization(), refreshAnomalies()]);
    $('month-detail').innerHTML='<span class="section-kicker">SELECT A MONTH</span><p>Select a coloured cell in the calendar to inspect separate MODIS and VIIRS counts and map detections for that month.</p>';
  } catch (err) { $('daily-readings').innerHTML='<div class="daily-empty">Daily readings could not be loaded. Apply filters to try again.</div>'; showError('Unable to load dashboard: '+err.message); }
  finally {setLoading(false);}
}
function renderStatus(s) {
  runtimeLimits={...runtimeLimits,...s};
  const e=$('api-state'); e.classList.add('connected');
  e.innerHTML=`<span class="status-dot"></span> Backend online · ${safe(s.version||'')}`;
  const docs=$('api-docs-link'); if(docs) docs.hidden=!s.docs_enabled;
  const warning=$('mode-warning');
  if (s.mode==='simulated') {
    warning.hidden=false;
    warning.innerHTML='<strong>SIMULATED PRACTICE DATA.</strong> All plotted observations were generated for interface testing. They are not NASA data, scientifically validated trends, or evidence of actual fires. Real CSV imports automatically take precedence.';
  } else if(s.mode==='empty') {
    warning.hidden=false;
    warning.innerHTML='<strong>No observations loaded.</strong> Import a NASA FIRMS CSV below, configure a FIRMS API key, or run <code>python scripts/seed_demo.py</code> to explore an explicitly simulated example.';
  } else { warning.hidden=true; }
  $('key-status').textContent=s.api_key_configured?'server key configured':'server key not configured';
  const backfillLimit=$('backfill-limit');
  if(backfillLimit) {
    backfillLimit.textContent=`Historical range backfill · max ${runtimeLimits.max_backfill_days} days per request`;
    if(!backfillLimit.dataset.initialized && runtimeLimits.max_backfill_days<30) {
      const endDate=new Date(`${$('backfill-end').value}T00:00:00Z`);
      endDate.setUTCDate(endDate.getUTCDate()-runtimeLimits.max_backfill_days+1);
      $('backfill-start').value=endDate.toISOString().slice(0,10);
      backfillLimit.dataset.initialized='true';
    }
  }
  const uploadLimit=$('upload-limit'); if(uploadLimit){const limit=`${(runtimeLimits.max_csv_upload_bytes/1_000_000).toFixed(0)} MB`;const note=s.deployment_platform==='vercel'?(s.large_csv_upload_ready?' via private Supabase Storage':' · configure Supabase Storage for 50 MB'):' locally';uploadLimit.textContent=`CSV upload · max ${limit}${note}`;}
  const exportLimit=$('export-limit'); if(exportLimit) exportLimit.textContent=`CSV export · max ${formatter.format(runtimeLimits.max_export_rows)} rows per request`;
  const vegetationLimit=$('vegetation-limit'); if(vegetationLimit) vegetationLimit.textContent=`Maximum 120 days · up to ${runtimeLimits.max_vegetation_composites} composites`;
  const admin=$('admin-token'); if(admin) admin.closest('.admin-token-row').hidden=!s.admin_required;
}
function renderProfile(profile) {
  const target=$('data-profile');
  if(!target) return;
  const modeLabel=profile.data_mode==='simulated'?'SIMULATED PRACTICE DATA':profile.data_mode.toUpperCase();
  const rows=profile.sources||[];
  target.innerHTML=`<div class="profile-heading"><b>${safe(modeLabel)}</b><span>${formatter.format(profile.total_observations||0)} loaded records</span></div>`+
    (rows.length?`<div class="profile-table-wrap"><table class="profile-table"><thead><tr><th>Source</th><th>Records</th><th>Positive days</th><th>Date span</th><th>FRP values</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${safe(r.source)}</td><td>${formatter.format(r.observations)}</td><td>${formatter.format(r.positive_days)}</td><td>${safe(r.first_detection||'N/A')} to ${safe(r.last_detection||'N/A')}</td><td>${formatter.format(r.records_with_frp||0)}</td></tr>`).join('')}</tbody></table></div>`:'<p>No observations are currently loaded.</p>')+
    `<p class="caveat">${safe(profile.interpretation)}</p>`;
}
function renderSummary(s) {
  $('total').textContent=formatter.format(s.total_detections);
  $('active-days').textContent=formatter.format(s.observed_positive_days);
  $('modis-total').textContent=formatter.format(s.by_sensor.find(x=>x.sensor==='MODIS')?.detections||0);
  $('viirs-total').textContent=formatter.format(s.by_sensor.find(x=>x.sensor==='VIIRS')?.detections||0);
}
function renderCalendar(payload, coverage) {
  const completed = new Set();
  for (const source of coverage.sources) for(const m of source.months) if(m.successful_query_days>0) completed.add(m.month);
  const source=payload.rows;
  const vals = new Map();
  for (const row of source) vals.set(row.month,(vals.get(row.month)||0)+row.detections);
  const max=Math.max(1,...vals.values());
  const startYear=Number(payload.start.slice(0,4)),endYear=Number(payload.end.slice(0,4));
  let html='<div class="calendar-grid"><span></span>';
  for (const m of monthNames) html+=`<div class="month-header">${m}</div>`;
  let months=0;
  for (let y=startYear;y<=endYear;y++) {
    html+=`<div class="year-label">${y}</div>`;
    for(let m=1;m<=12;m++){
      const k=`${y}-${String(m).padStart(2,'0')}`;
      const n=vals.get(k);
      const inRange= k>=payload.start.slice(0,7) && k<=payload.end.slice(0,7);
      if(n!==undefined&&inRange){
        const relative=Math.sqrt(n/max), bucket=Math.min(5,1+Math.floor(relative*5));
        const palette=['#27313a','#565046','#7f5c42','#a46c45','#ce8150','#f0a66c'];
        html+=`<button type="button" class="month-cell" style="background:${palette[bucket]};border-color:${palette[bucket]}" title="${k}: ${formatter.format(n)} loaded detections" aria-label="${monthNames[m-1]} ${y}: ${n} detections" data-month="${k}"></button>`;
        months++;
      } else if(inRange && completed.has(k)) {
        html+=`<button type="button" data-month="${k}" class="month-cell queried-empty" title="${k}: request completed but no loaded hotspots (not proof of zero fires)" aria-label="${monthNames[m-1]} ${y}: retrieved, no positive observations"></button>`;
        months++;
      } else {
        html+=`<button type="button" disabled class="month-cell" title="${k}: no confirmed data or retrieval" aria-label="${monthNames[m-1]} ${y}: unknown retrieval status"></button>`;
      }
    }
  }
  html+='</div>';
  $('heatmap').innerHTML=months?html:'<div class="empty-state">No positive fire detections were loaded for this filter. Import FIRMS observations or launch the simulated practice dataset.</div>';
  $('years-pill').textContent=`${startYear}–${endYear}`;
  document.querySelectorAll('[data-month]').forEach(b=>b.addEventListener('click',()=>selectMonth(b.dataset.month)));
}
async function selectMonth(month) {
  selectedMonth=month;
  document.querySelectorAll('[data-month]').forEach(b=>b.classList.toggle('selected',b.dataset.month===month));
  const start=month+'-01';
  const [year,mm]=month.split('-').map(Number);
  const end=new Date(Date.UTC(year,mm,0)).toISOString().slice(0,10);
  try {
    const params=urlParams({...currentFilter,start,end});
    const [detail,points,daily]=await Promise.all([
      api('/api/month-detail?'+urlParams({region:currentFilter.region,month})),
      api('/api/points?'+params+'&limit=2000'),
      api('/api/daily-readings?'+params)
    ]);
    const m=detail.by_sensor.MODIS, v=detail.by_sensor.VIIRS;
    $('month-detail').innerHTML=`<div class="detail-heading">${safe(monthNames[mm-1])} ${year} · Sensor breakdown</div><div class="detail-number"><span><i class="legend-dot modis"></i> MODIS: <b>${formatter.format(m.detections)}</b></span><span><i class="legend-dot viirs"></i> VIIRS: <b>${formatter.format(v.detections)}</b></span></div><p>${safe(detail.warning)}</p>`;
    $('map-subtitle').textContent=`Hotspots detected during ${monthNames[mm-1]} ${year}`;
    renderMap(points.points,points.total,points.truncated);
    renderComparison({by_sensor:[{sensor:'MODIS',detections:m.detections},{sensor:'VIIRS',detections:v.detections}]});
    renderDailyReadings(daily);
  }catch(e){showError('Month selection failed: '+e.message)}
}
async function refreshHarmonization() {
  const target=$('harmonization-status');
  target.textContent='Calculating overlap and chronological validation…';
  $('harmonization-stats').innerHTML='';
  $('harmonization-chart').innerHTML='';
  try {
    if(currentFilter.region==='all') {
      target.innerHTML='<strong>Choose a bounded study region.</strong> Calibration is region-specific; a global, mixed-region fit would be misleading.';
      return;
    }
    const params=urlParams({region:currentFilter.region,start:currentFilter.start,end:currentFilter.end,grid_km:$('grid-km').value,viirs_source:$('viirs-source').value});
    renderHarmonization(await api('/api/harmonization?'+params));
  } catch(e){target.textContent='Harmonization unavailable: '+e.message;}
}
function fmtLab(n, digits=2) {return n===null || n===undefined?'N/A':Number(n).toLocaleString('en-US',{maximumFractionDigits:digits});}
function renderHarmonization(result) {
  const target=$('harmonization-status'),metrics=$('harmonization-stats'),chart=$('harmonization-chart');
  const flag=result.status==='validated_on_holdout'?'pass':result.status==='rejected_on_holdout'?'fail':'pending';
  const titles={validated_on_holdout:'Candidate improved on unseen weeks',rejected_on_holdout:'Candidate rejected on unseen weeks',practice_only:'SIMULATED demonstration only',insufficient_pairs:'Insufficient paired retrieval weeks',unidentifiable:'Calibration is not identifiable',archive_exploratory:'Archive-based comparison · exploratory'};
  target.className='lab-status '+flag;
  const explain=result.status==='insufficient_pairs'||result.status==='unidentifiable'?' This lab requires overlapping MODIS_SP and VIIRS_*_SP observations plus completed region-specific API retrieval provenance for paired weeks. Near-real-time (NRT) CSV uploads alone cannot qualify.':'';
  target.innerHTML=`<div class="lab-status-heading"><span class="lab-dot"></span><strong>${safe(titles[result.status]||result.status)}</strong><span class="small-pill">${safe(result.data_mode.toUpperCase())}</span></div><p>${safe(result.message+explain)}</p>`;
  const c=result.calibration,v=result.validation;
  metrics.innerHTML=`<article class="lab-metric"><span>Eligible paired weeks</span><strong>${fmtLab(result.eligible_weeks,0)} <small>/ ${fmtLab(result.weeks_in_range,0)}</small></strong><p>${result.status==='archive_exploratory'?'Archive-positive pairs · not retrieval-verified':result.data_mode==='simulated'?'Positive-positive practice weeks':'Both sources · retrieval-verified weeks'}</p></article>
    <article class="lab-metric"><span>Training / held-out weeks</span><strong>${v&&c?`${c.training_weeks} <small>/ ${c.holdout_weeks}</small>`:'N/A'}</strong><p>Chronological split · no train/test leakage</p></article>
    <article class="lab-metric"><span>Candidate holdout MAE</span><strong>${fmtLab(v?.candidate?.mae)}</strong><p>10 km occupied cells per week</p></article>
    <article class="lab-metric"><span>Baseline holdout MAE</span><strong>${fmtLab(v?.scaled_baseline?.mae)}</strong><p>Training-fitted multiplicative scale</p></article>`;
  if(!v){
    const pairs=result.series||[];
    if(result.status==='archive_exploratory' && pairs.length){
      const ratio=result.calibration?.scaling_ratio;
      const max=Math.max(1,...pairs.flatMap(r=>[r.modis_cells,r.viirs_cells]));
      const W=790,H=220,L=44,R=16,T=18,B=35;
      const x=i=>L+(W-L-R)*(pairs.length===1?.5:i/(pairs.length-1));
      const y=n=>H-B-(H-T-B)*n/max;
      const line=k=>pairs.map((r,i)=>`${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`).join(' ');
      chart.innerHTML=`<div class="lab-chart-top"><div><b>Observed cross-sensor activity</b><p>${pairs.length} archive-positive paired weeks · descriptive comparison only</p></div></div>
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Observed MODIS and VIIRS occupied cells on paired positive weeks">
        ${[0,.25,.5,.75,1].map(t=>`<line x1="${L}" x2="${W-R}" y1="${y(max*t)}" y2="${y(max*t)}" stroke="#53646b" stroke-dasharray="3 6"/><text x="${L-7}" y="${y(max*t)+4}" text-anchor="end" fill="#8ba0a8" font-size="11">${Math.round(max*t)}</text>`).join('')}
        <polyline points="${line('modis_cells')}" fill="none" stroke="#f09a68" stroke-width="2.5"/>
        <polyline points="${line('viirs_cells')}" fill="none" stroke="#79c2d3" stroke-width="2.5"/>
        </svg>
        <p class="caveat">Orange: MODIS occupied cells. Blue: VIIRS occupied cells. ${ratio===undefined?'':`Descriptive median VIIRS/MODIS ratio: ${fmtLab(ratio,3)}.`} No holdout validation or proven sensor conversion. Weeks without positive observations in both sources are unknown, not zero.</p>
        <details class="manifest-details"><summary>Inspect observed paired weeks</summary><div class="lab-table-scroll"><table class="lab-table"><thead><tr><th>Week</th><th>MODIS cells</th><th>VIIRS cells</th><th>MODIS detections</th><th>VIIRS detections</th></tr></thead><tbody>${pairs.slice(-100).map(r=>`<tr><td>${safe(r.week)}</td><td>${fmtLab(r.modis_cells,0)}</td><td>${fmtLab(r.viirs_cells,0)}</td><td>${fmtLab(r.modis_detections,0)}</td><td>${fmtLab(r.viirs_detections,0)}</td></tr>`).join('')}</tbody></table></div></details>`;
    } else {
      chart.innerHTML=`<div class="lab-empty"><b>No comparable positive weeks available.</b><p>${safe(result.selection_policy||'')} Try a longer historical interval or another VIIRS platform. Missing observations are never treated as zero.</p></div>`;
    }
    return;
  }
  const test=result.series.filter(x=>x.partition==='holdout');
  const viirsName=result.source_policy.includes('NOAA20')?'VIIRS NOAA-20':result.source_policy.includes('NOAA21')?'VIIRS NOAA-21':'VIIRS S-NPP';
  const max=Math.max(1,...test.flatMap(x=>[x.viirs_cells,x.calibrated_estimate,x.scaled_baseline_estimate]));
  const W=790,H=230,px=36,py=22,innerW=W-2*px,innerH=H-2*py;
  const xy=(i,y)=>`${(px+innerW*(test.length===1?0:i/(test.length-1))).toFixed(1)},${(H-py-innerH*y/max).toFixed(1)}`;
  const path=k=>test.map((r,i)=>xy(i,r[k])).join(' ');
  const rows=test.slice(-8).map(r=>`<tr><td>${safe(r.week)}</td><td>${fmtLab(r.modis_cells,0)}</td><td>${fmtLab(r.viirs_cells,0)}</td><td>${fmtLab(r.calibrated_estimate)}</td><td>${fmtLab(r.scaled_baseline_estimate)}</td></tr>`).join('');
  chart.innerHTML=`<div class="lab-chart-top"><div><b>Unseen-week evaluation</b><p>Target is ${safe(viirsName)} occupied cells; estimates are not confirmed fire counts.</p></div><div class="lab-key"><span><i style="background:#79c2d3"></i> ${safe(viirsName)} observed</span><span><i style="background:#f09a68"></i> Candidate</span><span><i style="background:#8b9b9c"></i> Scaled baseline</span></div></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Held-out weekly VIIRS detections compared with candidate and scaled baseline">
    ${[0,.25,.5,.75,1].map(v=>`<line x1="${px}" x2="${W-px}" y1="${H-py-innerH*v}" y2="${H-py-innerH*v}" stroke="#324651" stroke-dasharray="3 6"/><text x="${px-8}" y="${H-py-innerH*v+4}" font-size="10" fill="#8ba0a8" text-anchor="end">${Math.round(max*v)}</text>`).join('')}
    <polyline points="${path('scaled_baseline_estimate')}" fill="none" stroke="#8b9b9c" stroke-width="2" stroke-dasharray="5 5"/>
    <polyline points="${path('calibrated_estimate')}" fill="none" stroke="#f09a68" stroke-width="2.5"/>
    <polyline points="${path('viirs_cells')}" fill="none" stroke="#79c2d3" stroke-width="2.5"/>
    ${test.map((r,i)=>`<circle cx="${xy(i,r.viirs_cells).split(',')[0]}" cy="${xy(i,r.viirs_cells).split(',')[1]}" r="3" fill="#79c2d3"><title>Week ${safe(r.week)}: VIIRS ${r.viirs_cells} cells</title></circle>`).join('')}</svg>
    <div class="lab-verdict">Holdout MAE improvement vs fitted baseline: <b>${fmtLab(v.mae_improvement_vs_scaled_pct)}%</b>. ${v.promoted?'Candidate passes the local comparison criterion.':'Candidate fails the promotion criterion.'} ${safe(v.interpretation)}</div>
    <details class="manifest-details"><summary>Inspect last eight held-out observations</summary><div class="lab-table-scroll"><table class="lab-table"><thead><tr><th>Week</th><th>MODIS</th><th>${safe(viirsName)}</th><th>Candidate</th><th>Baseline</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}


// Stage 4: retrospective, source-separated seasonal analysis. No calibrated
// estimates, significance or fire predictions are asserted in this interface.
async function refreshAnomalies() {
  const status=$('anomaly-status');
  status.className='lab-status';
  status.textContent='Computing leave-one-year-out seasonal reference…';
  $('anomaly-export').disabled=true;
  anomalyData=null;focusedWeek=null;
  $('anomaly-metrics').innerHTML='';
  $('anomaly-chart').innerHTML='<div class="lab-empty">Analysing paired weeks…</div>';
  $('anomaly-disagreement').innerHTML='';
  $('critical-periods').innerHTML='';
  $('anomaly-week-detail').textContent='Select a week to inspect its observation and reference.';
  const year=$('anomaly-year'), previous=year.value;
  year.disabled=true;
  try {
    if(currentFilter.region==='all') {
      status.textContent='Choose a bounded study region for the seasonal analysis.';
      return;
    }
    const params=urlParams({region:currentFilter.region,start:currentFilter.start,end:currentFilter.end,
      grid_km:$('grid-km').value,sensor:$('anomaly-sensor').value,threshold:$('anomaly-threshold').value,viirs_source:$('viirs-source').value,analysis_mode:$('anomaly-analysis-mode').value});
    const result=await api('/api/anomalies?'+params);
    anomalyData=result;
    $('anomaly-export').disabled=false;
    const practice=result.data_mode==='simulated';
    const headline={insufficient_history:'Insufficient comparison years',practice_only:'SIMULATED seasonal analysis · practice only',retrospective_comparison:'Retrospective seasonal comparison',archive_exploratory:'Archive-only · positive-observation exploration'};
    status.className='lab-status '+(practice?'fail':'pending');
    const historyNote=result.status==='insufficient_history'?' At least four independent reference years are required. Check selected region, source, and 2020–2024 date coverage.':'';
    status.innerHTML=`<div class="lab-status-heading"><span class="lab-dot"></span><strong>${safe(headline[result.status]||result.status)}</strong><span class="small-pill">${safe(result.sensor==='VIIRS'?viirsPlatformLabel(result.viirs_source):'MODIS · SP')} · ${safe(result.data_mode.toUpperCase())}</span></div><p>${safe(result.message+historyNote)} These are descriptive historical comparisons, not a real-time fire alert.</p>`;
    $('anomaly-metrics').innerHTML=[
      [result.analysis_mode==='archive_positive_only'?'Positive-observation weeks':'Paired eligible weeks',result.paired_weeks,result.analysis_mode==='archive_positive_only'?'Selected SP source only; other weeks unknown':'Standard-processed matched weeks'],
      ['Seasonally comparable',result.comparable_weeks,'≥4 other calendar years'],
      ['Elevated activity weeks',result.elevated_weeks,'Exploratory heuristic flags'],
      ['Missing reference',result.insufficient_reference_weeks,'Never imputed as zero']
    ].map(([label,note,foot])=>`<article class="lab-metric"><span>${safe(label)}</span><strong>${fmtLab(note,0)}</strong><p>${safe(foot)}</p></article>`).join('');
    const years=[...new Set(result.series.map(r=>r.year))].sort((a,b)=>b-a);
    year.innerHTML=years.length?years.map(y=>`<option value="${y}">${y}</option>`).join(''):'<option value="">No data</option>';
    if(years.length){year.value=years.includes(Number(previous))?previous:String(years[0]);year.disabled=false;}
    drawAnomalyYear();
    const periods=result.critical_periods;
    $('critical-periods').innerHTML=periods.length?periods.slice(0,8).map(p=>`<button type="button" class="period-card" data-period-week="${safe(p.peak_week)}"><span><b>${safe(p.start)} to ${safe(p.end)}</b><small>${p.weeks} elevated week(s) · peak ${fmtLab(p.peak_score)} robust score</small></span><span>Inspect</span></button>`).join(''):'<p class="anomaly-empty-text">No sustained elevated periods were flagged with the available reference and selected sensitivity. This does not prove there were no unusual fires.</p>';
    document.querySelectorAll('[data-period-week]').forEach(b=>b.addEventListener('click',()=>{
      const selected=result.series.find(r=>r.week===b.dataset.periodWeek);
      if(!selected)return;
      year.value=String(selected.year);drawAnomalyYear();showAnomalyWeek(selected.week);
      $('anomaly-panel').scrollIntoView({behavior:'smooth',block:'start'});
    }));
  } catch(err){status.textContent='Anomaly analysis unavailable: '+err.message;}
}
function seasonalSvg(rows) {
  if(!rows.length)return '<div class="lab-empty">No eligible paired weeks for this year.</div>';
  const W=850,H=290,L=48,R=18,T=18,B=39, plotW=W-L-R,plotH=H-T-B;
  const max=Math.max(1,...rows.flatMap(r=>[r.observed_cells,r.seasonal_p90||0]))*1.12;
  const xx=(r)=>L+(r.iso_week-1)/52*plotW;
  const yy=(v)=>T+plotH*(1-v/max);
  const compared=rows.filter(r=>r.baseline_median!==null);
  // Split baseline/envelope into contiguous full ISO week segments; never bridge missing weeks.
  const segments=[];let segment=[];
  for(const r of compared){
    if(segment.length && (Date.parse(r.week)-Date.parse(segment[segment.length-1].week))!==7*86400000){segments.push(segment);segment=[];}
    segment.push(r);
  }
  if(segment.length)segments.push(segment);
  // Other sensors remain separate; the envelope is historical inter-year spread.
  const band=segments.map(s=>`<polygon points="${s.map(r=>`${xx(r)},${yy(r.seasonal_p90)}`).join(' ')} ${s.slice().reverse().map(r=>`${xx(r)},${yy(r.seasonal_p10)}`).join(' ')}" fill="#3a7378" opacity=".38"/>`).join('');
  const baseline=segments.map(s=>`<polyline points="${s.map(r=>`${xx(r)},${yy(r.baseline_median)}`).join(' ')}" fill="none" stroke="#84bfc0" stroke-width="2" stroke-dasharray="5 5"/>`).join('');
  const observed=rows.map(r=>`<circle class="weekly-point" data-week="${safe(r.week)}" cx="${xx(r)}" cy="${yy(r.observed_cells)}" r="${r.elevated?5:3.3}" fill="${r.elevated?'#fa8568':'#f0ae76'}" stroke="#14232c" stroke-width="1.5" tabindex="0" role="button" aria-label="${safe(r.week)}: ${r.observed_cells} occupied cells"><title>${safe(r.week)}: ${r.observed_cells} occupied cells${r.elevated?' · elevated':''}</title></circle>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Observed weekly occupied cells compared with empirical historical year-to-year seasonal envelope">
    ${[0,.25,.5,.75,1].map(f=>`<line x1="${L}" y1="${yy(f*max)}" x2="${W-R}" y2="${yy(f*max)}" stroke="#344550" stroke-dasharray="3 6"/><text x="${L-8}" y="${yy(f*max)+4}" text-anchor="end" fill="#839ba7" font-size="10">${Math.round(max*f)}</text>`).join('')}
    ${[1,13,26,39,52].map(w=>`<text x="${L+(w-1)/52*plotW}" y="${H-12}" text-anchor="middle" fill="#91a7b0" font-size="11">W${w}</text>`).join('')}
    ${band}${baseline}${observed}</svg>`;
}
function drawAnomalyYear() {
  const data=anomalyData;
  if(!data) return;
  const year=Number($('anomaly-year').value);
  const rows=data.series.filter(r=>r.year===year);
  const container=$('anomaly-chart');
  container.innerHTML=seasonalSvg(rows);
  const diff=$('anomaly-disagreement');
  if(!rows.length){diff.innerHTML='<div class="lab-empty">No comparisons for this year.</div>';return;}
  const validDiffs=rows.filter(r=>r.sensor_difference_cells!==null);
  // Always register observed-week interactions, even without comparison-sensor data.
  container.querySelectorAll('[data-week]').forEach(b=>{
    b.addEventListener('click',()=>showAnomalyWeek(b.dataset.week));
    b.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();showAnomalyWeek(b.dataset.week);}});
  });
  if(!validDiffs.length){
    diff.innerHTML='<p class="caveat">No other-sensor positive observations in these weeks; sensor differences are unavailable, not zero.</p>';
    const chosen=rows.find(r=>r.week===focusedWeek);
    showAnomalyWeek(chosen?chosen.week:rows[rows.length-1].week);
    return;
  }
  const max=Math.max(1,...validDiffs.map(r=>Math.abs(r.sensor_difference_cells)));
  diff.innerHTML=`<div class="anomaly-diff-list">${validDiffs.map(r=>`<button type="button" class="diff-item" data-week="${safe(r.week)}" title="${safe(r.week)} · ${safe(viirsPlatformLabel(data.viirs_source))} minus MODIS: ${(data.sensor==='VIIRS'?r.sensor_difference_cells:-r.sensor_difference_cells)}"><span class="diff-date">${safe(r.week.slice(5))}</span><span class="diff-track"><i style="width:${Math.max(1,100*Math.abs(r.sensor_difference_cells)/max)}%;background:${(data.sensor==='VIIRS'?r.sensor_difference_cells:-r.sensor_difference_cells)<0?'#d29c7b':'#78b7ce'}"></i></span><span class="diff-num">${fmtLab(data.sensor==='VIIRS'?r.sensor_difference_cells:-r.sensor_difference_cells,0)}</span></button>`).join('')}</div><p class="caveat">Signed ${safe(viirsPlatformLabel(data.viirs_source))} − MODIS occupied-cell difference. Bars compare observations only; they are not model residuals or uncertainty intervals.</p>`;
  diff.querySelectorAll('[data-week]').forEach(b=>b.addEventListener('click',()=>showAnomalyWeek(b.dataset.week)));
  const chosen=rows.find(r=>r.week===focusedWeek);
  showAnomalyWeek(chosen?chosen.week:rows[rows.length-1].week);
}
function showAnomalyWeek(week) {
  if(!anomalyData)return;
  const r=anomalyData.series.find(x=>x.week===week);
  if(!r)return;
  focusedWeek=week;
  document.querySelectorAll('.weekly-point,.diff-item').forEach(x=>x.classList.toggle('is-selected',x.dataset.week===week));
  const same=r.baseline_median!==null;
  $('anomaly-week-detail').innerHTML=`<b>Week of ${safe(r.week)}</b><div class="week-details"><span>Observed <strong>${fmtLab(r.observed_cells,0)}</strong></span><span>Other sensor <strong>${r.other_sensor_cells===null?'Unknown':fmtLab(r.other_sensor_cells,0)}</strong></span><span>Historical median <strong>${fmtLab(r.baseline_median)}</strong></span><span>Reference years <strong>${r.reference_years}</strong></span></div><p>${same?`Historical P10–P90: <b>${fmtLab(r.seasonal_p10)}–${fmtLab(r.seasonal_p90)}</b> occupied cells. Robust score: <b>${fmtLab(r.robust_score)}</b>. ${r.elevated?'<strong>Elevated activity heuristic triggered.</strong>':'Not flagged as elevated.'}`:'Insufficient independent historical years; no anomaly claim.'}</p>`;
}
function exportAnomalyJSON() {
  if(!anomalyData)return;
  const content=JSON.stringify(anomalyData,null,2);
  const uri=URL.createObjectURL(new Blob([content],{type:'application/json'}));
  const a=document.createElement('a');
  a.href=uri;a.download=`emberlens-stage6-${anomalyData.region}-${anomalyData.sensor.toLowerCase()}-${String(anomalyData.viirs_source||'viirs').toLowerCase()}.json`;
  document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(uri);
}

function renderCoverage(payload) {
  const target=$('coverage-summary');
  const items=payload.sources;
  const sourceTotal=items.reduce((sum,item)=>sum+Number(item.summary.detections||0),0);
  let html=sourceTotal===0?'<div class="overlap-warning">No imported detections match this study region and date range. Verify the selected location, dates, and Supabase import status. The import history below may still list files loaded for other places or dates.</div>':'<div class="overlap-warning">This audit shows observed positive detections and retrieval provenance, not a complete record of satellite passes. Uploading a FIRMS CSV does not certify no-fire days.</div>';
  html+='<div class="coverage-columns">';
  for(const s of items) {
    const c=s.summary, eligible=Math.max(0,c.days_in_period-c.outside_product_window);
    const share=(n)=>eligible?Math.min(100,100*n/eligible).toFixed(2)+'%':'0%';
    const label=sourceLabel(s.source);
    const observed=c.positive_days, queriedOnly=c.queried_without_detections;
    html+=`<div class="coverage-item">
      <div class="coverage-header"><strong>${safe(label)}</strong><span>${formatter.format(c.detections)} records</span></div>
      <div class="coverage-track" role="img" aria-label="${safe(label)}: ${observed} positive dates, ${queriedOnly} retrieved without positive records, ${c.unverified_days} unverified days">
        <span class="coverage-positive" style="width:${share(observed)}"></span><span class="coverage-queried" style="width:${share(queriedOnly)}"></span><span class="coverage-unknown" style="width:${share(c.unverified_days)}"></span>
      </div>
      <div class="coverage-metrics"><span><i class="coverage-dot positive"></i> ${observed} positive</span><span><i class="coverage-dot queried"></i> ${queriedOnly} retrieved / no records</span><span><i class="coverage-dot unknown"></i> ${c.unverified_days} unverified</span></div>
      <div class="coverage-footnote">${c.successful_query_days} days queried · ${c.outside_product_window} outside known product dates${s.availability?' · NASA product dates checked':' · API product dates not yet verified'}</div>
    </div>`;
  }
  html+='</div>';
  const overlap=payload.nrt_sp_overlap_positive_days||{};
  if((overlap.MODIS||0)+(overlap.VIIRS||0)>0){
    html+=`<div class="overlap-warning">⚠ Potential NRT/SP overlap: MODIS ${overlap.MODIS||0} same-date periods; VIIRS ${overlap.VIIRS||0}. Raw sensor totals may double-count observations. Scope a single processing class before scientific comparison.</div>`;
  }
  target.innerHTML=html;
  const archive=$('archive-manifests');
  archive.innerHTML=payload.recent_archive_imports.length?payload.recent_archive_imports.map(x=>`<div class="manifest-row"><b>${safe(x.source)}</b> · ${safe(x.filename)}<span>${formatter.format(x.rows_added)} new · ${formatter.format(x.rows_invalid)} invalid · ${safe(x.first_date||'no positive dates')} to ${safe(x.last_date||'N/A')}</span></div>`).join(''):'No historical archive imports recorded yet.';
}
function renderComparison(summary) {
  const n=summary.by_sensor.find(x=>x.sensor==='MODIS')?.detections||0;
  const v=summary.by_sensor.find(x=>x.sensor==='VIIRS')?.detections||0;
  const max=Math.max(1,n,v);
  $('modis-label').textContent=formatter.format(n);
  $('viirs-label').textContent=formatter.format(v);
  $('modis-bar').style.width=`${100*n/max}%`;
  $('viirs-bar').style.width=`${100*v/max}%`;
}
function formatChange(change) {
  if (!change) return 'Not available';
  const sign=change.delta>0?'+':'';
  const pct=change.percent_change===null?'from a zero-record earlier period':`${sign}${change.percent_change}%`;
  return `${sign}${formatter.format(change.delta)} records (${pct})`;
}
function syncChangePeriods() {
  if (!currentFilter.start || !$('change-before-start')) return;
  const laterStart=new Date(`${currentFilter.start}T00:00:00Z`);
  const laterEnd=new Date(`${currentFilter.end}T00:00:00Z`);
  const duration=Math.round((laterEnd-laterStart)/86400000);
  const earlierEnd=new Date(laterStart.getTime()-86400000);
  const earlierStart=new Date(earlierEnd.getTime()-duration*86400000);
  $('change-before-start').value=earlierStart.toISOString().slice(0,10);
  $('change-before-end').value=earlierEnd.toISOString().slice(0,10);
  $('change-after-start').value=currentFilter.start;
  $('change-after-end').value=currentFilter.end;
  changeExplanation=null;
  $('export-evidence').disabled=true;
  $('change-explanation-result').innerHTML='<b>Ready to compare matching periods.</b><span>Change the dates if needed, then test whether the loaded-record change remains after matching sources.</span>';
}
function renderChangeExplanation(result) {
  const target=$('change-explanation-result');
  const raw=result.raw_loaded_detection_change;
  const matched=result.matched_positive_source_change;
  const evidence=result.source_evidence;
  const title={
    direction_survives_matched_sources:'Direction survives the source match',
    source_mix_changed:'Source mix changed between periods',
    direction_changes_or_is_flat_when_matched:'Direction changes after the source match',
    no_raw_change:'No raw count change to explain',
    not_comparable:'No like-for-like source comparison is available'
  }[result.assessment.state]||'Comparison complete';
  const sourceRows=(result.source_rows||[]).map(row=>`<tr><td><b>${safe(row.label)}</b><small>${safe(row.source)}</small></td><td>${formatter.format(row.before.detections)}<small>${formatter.format(row.before.positive_days)} positive days; ${formatter.format(row.before.retrieved_request_days)} request days</small></td><td>${formatter.format(row.after.detections)}<small>${formatter.format(row.after.positive_days)} positive days; ${formatter.format(row.after.retrieved_request_days)} request days</small></td><td>${safe(formatChange(row.change))}<small>${row.positive_records_in_both_periods?'Positive records in both periods':'Not eligible for the matched subtotal'}</small></td></tr>`).join('');
  const matchedText=matched?`${formatter.format(matched.before)} to ${formatter.format(matched.after)} · ${safe(formatChange(matched))}`:'No shared sources with positive loaded records';
  target.innerHTML=`<div class="change-verdict ${safe(result.assessment.state)}"><span class="section-kicker">${safe(result.method_version)} · ${safe(result.data_mode.toUpperCase())}</span><h3>${safe(title)}</h3><p>${safe(result.assessment.summary)}</p></div>
    <div class="change-metrics"><article><span>All loaded records</span><strong>${formatter.format(raw.before)} → ${formatter.format(raw.after)}</strong><small>${safe(formatChange(raw))}</small></article><article><span>Matched positive sources</span><strong>${matched?`${formatter.format(matched.before)} → ${formatter.format(matched.after)}`:'N/A'}</strong><small>${matchedText}</small></article><article><span>Sources in both periods</span><strong>${formatter.format(evidence.matching_positive_sources.length)}</strong><small>${evidence.matching_positive_sources.length?safe(evidence.matching_positive_sources.join(', ')):'No matching sources'}</small></article></div>
    <div class="change-source-table-wrap"><table class="change-source-table"><thead><tr><th>Source</th><th>Earlier period</th><th>Later period</th><th>Change</th></tr></thead><tbody>${sourceRows||'<tr><td colspan="4">No loaded positive records in either selected period.</td></tr>'}</tbody></table></div>
    <p class="change-retrieval-note">“Request days” are completed region-specific FIRMS retrieval days. They are not satellite coverage. Sources only earlier: ${safe(evidence.sources_only_before.join(', ')||'none')}. Sources only later: ${safe(evidence.sources_only_after.join(', ')||'none')}.</p>`;
}
async function runChangeExplanation() {
  const button=$('explain-change');
  const before_start=$('change-before-start').value, before_end=$('change-before-end').value;
  const after_start=$('change-after-start').value, after_end=$('change-after-end').value;
  if (!before_start || !before_end || !after_start || !after_end || before_start>before_end || after_start>after_end) {
    $('change-explanation-result').textContent='Choose two valid date ranges.';
    return;
  }
  button.disabled=true; button.textContent='Comparing...';
  $('export-evidence').disabled=true;
  $('change-explanation-result').textContent='Checking source-specific loaded records and retrieval provenance...';
  try {
    const result=await api('/api/change-explanation?'+urlParams({region:currentFilter.region,sensor:currentFilter.sensor,before_start,before_end,after_start,after_end}));
    changeExplanation=result;
    renderChangeExplanation(result);
    $('export-evidence').disabled=false;
  } catch (error) {
    changeExplanation=null;
    $('change-explanation-result').textContent=`Comparison unavailable: ${error.message}`;
  } finally { button.disabled=false; button.textContent='Explain change'; }
}
function evidenceReport(result) {
  const raw=result.raw_loaded_detection_change, matched=result.matched_positive_source_change;
  const sourceRows=result.source_rows.map(row=>`| ${row.source} | ${row.before.detections} | ${row.after.detections} | ${row.change.delta >= 0?'+':''}${row.change.delta} | ${row.positive_records_in_both_periods?'yes':'no'} |`).join('\n');
  return `# EmberLens evidence report\n\nGenerated locally: ${new Date().toISOString()}\n\n## Reproducible query\n\n- Method: ${result.method_version}\n- Data mode: ${result.data_mode}\n- Region: ${result.region}\n- Sensor filter: ${result.sensor}\n- Earlier period: ${result.before.start} to ${result.before.end}\n- Later period: ${result.after.start} to ${result.after.end}\n\n## Result\n\n- Assessment: ${result.assessment.state}\n- Summary: ${result.assessment.summary}\n- All loaded detection records: ${raw.before} to ${raw.after} (${raw.delta >= 0?'+':''}${raw.delta})\n- Matched positive sources: ${matched?`${matched.before} to ${matched.after} (${matched.delta >= 0?'+':''}${matched.delta})`:'not available'}\n- Sources with positive records in both periods: ${result.source_evidence.matching_positive_sources.join(', ')||'none'}\n\n## Source evidence\n\n| Source | Earlier records | Later records | Delta | Positive records in both periods |\n| --- | ---: | ---: | ---: | --- |\n${sourceRows}\n\n## Limitations\n\n${result.limitations.map(item=>`- ${item}`).join('\n')}\n`;
}
function exportEvidenceReport() {
  if (!changeExplanation) return;
  const blob=new Blob([evidenceReport(changeExplanation)],{type:'text/markdown;charset=utf-8'});
  const url=URL.createObjectURL(blob), link=document.createElement('a');
  link.href=url;
  link.download=`emberlens-evidence-${changeExplanation.region}-${changeExplanation.after.end}.md`;
  document.body.appendChild(link); link.click(); link.remove();
  URL.revokeObjectURL(url);
}
function renderDailyReadings(payload) {
  const target=$('daily-readings');
  const range=$('daily-readings-range');
  range.textContent=`${payload.start} to ${payload.end} · ${payload.data_mode} data`;
  const rows=payload.rows||[];
  if (!rows.length) {
    target.innerHTML='<div class="daily-empty">No daily sensor readings are available for these filters.</div>';
    return;
  }
  const W=Math.max(320,Math.round(target.getBoundingClientRect().width||900)),H=286,L=46,R=18,T=18,B=38;
  const start=Date.parse(`${payload.start}T00:00:00Z`),end=Date.parse(`${payload.end}T00:00:00Z`);
  const span=Math.max(86400000,end-start);
  const max=Math.max(1,...rows.map(row=>row.detections));
  const x=date=>L+(Date.parse(`${date}T00:00:00Z`)-start)/span*(W-L-R);
  const y=value=>T+(1-value/max)*(H-T-B);
  const bySensor={MODIS:[],VIIRS:[]};
  for (const row of rows) if (bySensor[row.sensor]) bySensor[row.sensor].push(row);
  const colors={MODIS:'#f09a65',VIIRS:'#70c6cc'};
  const lines=[];
  const dots=[];
  const legend=[];
  for (const sensor of Object.keys(bySensor)) {
    const series=bySensor[sensor];
    if (!series.length) continue;
    legend.push(`<span><i style="background:${colors[sensor]}"></i>${sensor}</span>`);
    let segment=[];
    const drawSegment=()=>{
      if (segment.length>1) lines.push(`<polyline points="${segment.map(row=>`${x(row.date).toFixed(1)},${y(row.detections).toFixed(1)}`).join(' ')}" fill="none" stroke="${colors[sensor]}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>`);
      segment=[];
    };
    for (const row of series) {
      if (segment.length && Date.parse(`${row.date}T00:00:00Z`)-Date.parse(`${segment[segment.length-1].date}T00:00:00Z`)!==86400000) drawSegment();
      segment.push(row);
      dots.push(`<circle cx="${x(row.date).toFixed(1)}" cy="${y(row.detections).toFixed(1)}" r="3.4" fill="${colors[sensor]}"><title>${sensor}, ${row.date}: ${formatter.format(row.detections)} loaded detection records</title></circle>`);
    }
    drawSegment();
  }
  const yTicks=[0,.5,1].map(f=>`<g><line x1="${L}" y1="${y(max*f).toFixed(1)}" x2="${W-R}" y2="${y(max*f).toFixed(1)}" stroke="#466076" stroke-dasharray="3 5"/><text x="${L-9}" y="${(y(max*f)+4).toFixed(1)}" text-anchor="end" fill="#bed1e2" font-size="11">${formatter.format(Math.round(max*f))}</text></g>`).join('');
  const dates=[payload.start,new Date((start+end)/2).toISOString().slice(0,10),payload.end];
  const xTicks=dates.map(date=>`<text x="${x(date).toFixed(1)}" y="${H-12}" text-anchor="middle" fill="#bed1e2" font-size="11">${date}</text>`).join('');
  target.innerHTML=`<div class="daily-legend">${legend.join('')}</div><div class="daily-chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily MODIS and VIIRS loaded active-fire detection records. Missing dates are not shown as zero.">${yTicks}${xTicks}${lines.join('')}${dots.join('')}</svg></div>`;
}
function renderMap(points,total,truncated) {
  const parent=$('map-plot');
  $('map-count').textContent=`${formatter.format(points.length)} plotted${truncated?' (sample)':''}`;
  const region=regions.find(x=>x.id===selectedRegion)||regions[0];
  if(!region) { parent.innerHTML='<div class="empty-state">Region unavailable</div>';return; }
  const [west,south,east,north]=region.bbox;
  const W=800,H=430,pad=47;
  const x=lon=>pad+(lon-west)/(east-west)*(W-2*pad);
  const y=lat=>H-pad-(lat-south)/(north-south)*(H-2*pad);
  const lines=[];
  for(let i=0;i<=5;i++){
    const xx=pad+(W-2*pad)*i/5,yy=pad+(H-2*pad)*i/5;
    const lat=north-(north-south)*i/5,lon=west+(east-west)*i/5;
    lines.push(`<line x1="${xx}" y1="${pad}" x2="${xx}" y2="${H-pad}" stroke="#38505a" stroke-dasharray="3 6" opacity=".6"/><line x1="${pad}" y1="${yy}" x2="${W-pad}" y2="${yy}" stroke="#38505a" stroke-dasharray="3 6" opacity=".6"/><text x="${xx}" y="${H-18}" text-anchor="middle" fill="#77919c" font-size="11">${lon.toFixed(1)}°</text><text x="9" y="${yy+4}" fill="#77919c" font-size="11">${lat.toFixed(1)}°</text>`);
  }
  const circles=points.slice().reverse().filter(p=>p.longitude>=west&&p.longitude<=east&&p.latitude>=south&&p.latitude<=north).map(p=>{
    const modis=p.sensor==='MODIS';const radius=modis?3.9:2.8;
    const color=modis?'#ef966a':'#81c6dc';
    return `<circle cx="${x(p.longitude).toFixed(2)}" cy="${y(p.latitude).toFixed(2)}" r="${radius}" fill="${color}" opacity=".69"><title>${safe(p.sensor)} · ${safe(p.acq_date)} · ${Number(p.latitude).toFixed(4)}, ${Number(p.longitude).toFixed(4)}</title></circle>`;
  }).join('');
  parent.innerHTML=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Longitude-latitude geographic coordinate plot">
    <defs><radialGradient id="glow"><stop offset="0" stop-color="#2c4b53" stop-opacity=".8"/><stop offset="1" stop-color="#162630" stop-opacity="0"/></radialGradient></defs>
    <ellipse cx="500" cy="170" rx="320" ry="225" fill="url(#glow)"/>
    ${lines.join('')}
    <rect x="${pad}" y="${pad}" width="${W-2*pad}" height="${H-2*pad}" fill="none" stroke="#4b626a" stroke-opacity=".5"/>
    <text x="${pad+12}" y="${pad+24}" fill="#b5c9cf" font-size="13" font-weight="bold">${safe(region.name)}</text>
    <text x="${pad+12}" y="${pad+42}" fill="#829ca4" font-size="10">POSITION PLOT · NOT A SATELLITE BASEMAP</text>
    ${circles}
    ${points.length? '': `<text x="400" y="215" text-anchor="middle" fill="#92aab2" font-size="16">No loaded detections for this selection</text>`}
    </svg>`;
}
async function uploadFile() {
  const file=$('csv-file').files[0];
  if(!file){setImportMessage('Choose a FIRMS CSV first.',true);return;}
  if(!file.name.toLowerCase().endsWith('.csv')){setImportMessage('Choose a .csv file. Compressed archives can be imported with the archive tool.',true);return;}
  if(file.size>runtimeLimits.max_csv_upload_bytes){setImportMessage(`Maximum CSV size is ${(runtimeLimits.max_csv_upload_bytes/1_000_000).toFixed(0)} MB on this deployment.`,true);return;}
  $('upload-btn').disabled=true;
  try{
    const source=$('import-source').value;
    let result;
    if(runtimeLimits.deployment_platform==='vercel' && runtimeLimits.large_csv_upload_ready){
      setImportMessage('Preparing a secure direct upload to Supabase Storage…');
      const ticket=await api('/api/import/csv/upload-url',{method:'POST',headers:importHeaders('application/json'),body:JSON.stringify({source,filename:file.name,size:file.size})});
      if(window.tus?.Upload && ticket.upload_token && ticket.tus_endpoint){
        await new Promise((resolve,reject)=>{
          const transfer=new window.tus.Upload(file,{
            endpoint:ticket.tus_endpoint,
            retryDelays:[0,3000,5000,10000,20000],
            headers:{'x-signature':ticket.upload_token},
            metadata:{bucketName:ticket.bucket,objectName:ticket.path,contentType:'text/csv',cacheControl:'3600'},
            chunkSize:6*1024*1024,
            uploadDataDuringCreation:true,
            removeFingerprintOnSuccess:true,
            onError:reject,
            onProgress:(uploaded,total)=>setImportMessage(`Uploading to Supabase… ${Math.round(uploaded/total*100)}%`),
            onSuccess:resolve
          });
          transfer.start();
        });
      }else{
        setImportMessage(`Uploading ${(file.size/1_000_000).toFixed(1)} MB directly to private storage…`);
        const uploaded=await fetch(ticket.upload_url,{method:'PUT',headers:{'content-type':'text/csv'},body:file});
        if(!uploaded.ok){
          let detail='';try{detail=(await uploaded.text()).slice(0,240)}catch{}
          throw new Error(`Supabase Storage upload failed (${uploaded.status}). ${detail}`.trim());
        }
      }
      setImportMessage('Upload complete. Validating and importing detections…');
      result=await api('/api/import/csv/complete',{method:'POST',headers:importHeaders('application/json'),body:JSON.stringify({source,filename:file.name,path:ticket.path})});
    }else{
      setImportMessage('Uploading and normalizing observations…');
      const query=urlParams({source,filename:file.name});
      result=await api('/api/import/csv?'+query,{method:'POST',headers:importHeaders('text/csv'),body:await file.text()});
    }
    setImportMessage(`Imported ${formatter.format(result.rows_added)} new records (${result.rows_invalid} invalid rows omitted).`);
    await refresh();
  }catch(e){setImportMessage(e.message,true)}
  finally{$('upload-btn').disabled=false}
}
async function fetchFirms() {
  if($('region').value==='all'){setImportMessage('Choose a bounded region first.',true);return;}
  $('fetch-btn').disabled=true;setImportMessage('Requesting official FIRMS observations…');
  try{
    const payload={region:$('region').value,source:$('import-source').value,start_date:$('fetch-date').value,days:Number($('fetch-days').value)};
    const result=await api('/api/import/firms',{method:'POST',headers:importHeaders('application/json'),body:JSON.stringify(payload)});
    setImportMessage(`Fetched ${formatter.format(result.rows_added)} new observations. ${result.retrieval_days} retrieval day(s) recorded (not complete satellite coverage).`);
    await refresh();
  }catch(e){setImportMessage(e.message,true)}
  finally{$('fetch-btn').disabled=false}
}
async function backfillFirms() {
  if($('region').value==='all'){setImportMessage('Select a bounded region first.',true);return;}
  const btn=$('backfill-btn');btn.disabled=true;
  const start=$('backfill-start').value, end=$('backfill-end').value;
  const requestedDays=Math.floor((new Date(`${end}T00:00:00Z`)-new Date(`${start}T00:00:00Z`))/86400000)+1;
  if(!Number.isFinite(requestedDays)||requestedDays<1||requestedDays>runtimeLimits.max_backfill_days){
    btn.disabled=false;setImportMessage(`Choose a valid past interval of up to ${runtimeLimits.max_backfill_days} days per request.`,true);return;
  }
  setImportMessage('Retrieving FIRMS data in a bounded request…');
  try {
    const payload={region:$('region').value,source:$('import-source').value,
      start_date:start,end_date:end};
    const result=await api('/api/import/firms-range',{method:'POST',headers:importHeaders('application/json'),body:JSON.stringify(payload)});
    setImportMessage(`${result.status}: ${result.windows_imported.length} windows imported, ${result.windows_skipped.length} already retrieved, ${result.windows_failed.length} failed; ${formatter.format(result.total_rows_added)} new hotspots. Retrieval ≠ full satellite coverage.`,result.status==='partial');
    await refresh();
  }catch(e){setImportMessage(e.message,true)}
  finally {btn.disabled=false}
}
async function checkAvailability() {
  const btn=$('availability-btn');btn.disabled=true;setImportMessage('Checking NASA FIRMS advertised product dates…');
  try {
    const result=await api('/api/availability/refresh',{method:'POST',headers:importHeaders()});
    setImportMessage(`NASA lists ${result.datasets_updated} supported datasets. These dates are not regional satellite coverage.`);
    await refresh();
  }catch(e){setImportMessage(e.message,true)}
  finally {btn.disabled=false}
}
function contextDateRange() {
  const start=$('context-start').value, end=$('context-end').value;
  if(!start||!end||start>end) throw new Error('Choose a valid environmental-context date range.');
  return {region:$('region').value,start_date:start,end_date:end};
}
function renderProviderStatus(status) {
  const weather=$('weather-provider-status');
  if(weather){
    weather.textContent=status.nasa_power_available?'Free public API':'Status unavailable';
    weather.classList.toggle('configured',status.nasa_power_available);
  }
  const vegetation=$('vegetation-provider-status');
  if(vegetation) vegetation.textContent='Public STAC + Data API';
}
async function loadWeatherContext() {
  const button=$('load-weather');button.disabled=true;
  $('weather-status').textContent='Requesting regional weather time series…';
  $('weather-results').innerHTML='';
  try {
    const query=urlParams(contextDateRange());
    const result=await api('/api/context/weather?'+query);
    $('weather-status').textContent=`${result.observations.length} daily weather records · ${safe(result.region)} centroid · ${result.stored?'stored backfill':result.cached?'cached':'NASA POWER API'}`;
    const labels={'t_2m:C':'Temp °C','relative_humidity_2m:p':'Humidity %','wind_speed_10m:ms':'Wind m/s','wind_dir_10m:d':'Wind °','precip_6h:mm':'Rain mm / 6h','precipitation:mm/day':'Rain mm / day'};
    const rows=result.observations.map(o=>`<tr><td>${safe(o.datetime.replace('T',' ').replace('Z',' UTC'))}</td>${result.parameters.map(p=>`<td>${o[p]===null||o[p]===undefined?'N/A':fmtLab(o[p],1)}</td>`).join('')}</tr>`).join('');
    $('weather-results').innerHTML=`<div class="context-table-wrap"><table class="context-table"><thead><tr><th>Time</th>${result.parameters.map(p=>`<th>${labels[p]||safe(p)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div><p class="context-note">${safe(result.interpretation)}</p>`;
  } catch(e) { $('weather-status').textContent=e.message; }
  finally {button.disabled=false;}
}
async function loadVegetationContext() {
  const button=$('load-vegetation');button.disabled=true;
  $('vegetation-status').textContent='Searching MODIS vegetation composites and calculating area statistics…';
  $('vegetation-results').innerHTML='';
  try {
    const query=urlParams(contextDateRange());
    const result=await api('/api/context/vegetation?'+query);
    const rows=result.composites.map(c=>`<tr><td>${safe(c.date||c.datetime||c.item_id)}</td><td>${fmtLab(c.mean_ndvi,3)}</td><td>${fmtLab(c.p10_ndvi,3)}</td><td>${fmtLab(c.p90_ndvi,3)}</td><td>${c.valid_percent===null?'N/A':fmtLab(c.valid_percent,1)+'%'}</td></tr>`).join('');
    $('vegetation-status').textContent=result.composites.length?`${result.composites.length} MODIS composites found · ${safe(result.collection)}${result.cached?' · cached':''}`:'No MODIS vegetation composites found for this region and date range.';
    $('vegetation-results').innerHTML=result.composites.length?`<div class="context-table-wrap"><table class="context-table"><thead><tr><th>Composite</th><th>Mean NDVI</th><th>P10</th><th>P90</th><th>Valid sample px</th></tr></thead><tbody>${rows}</tbody></table></div><p class="context-note">${safe(result.interpretation)}</p>`:`<p class="context-note">Try widening the date range. The collection is a 16-day composite, so it will not necessarily have an item for every day.</p>`;
  } catch(e) { $('vegetation-status').textContent=e.message; }
  finally {button.disabled=false;}
}
async function backfillFreeContext() {
  const button=$('backfill-free-context');
  const status=$('free-context-status');
  button.disabled=true;
  const regions=['punjab','california','amazon'];
  const batches=[];
  for(const region of regions) for(let year=2020;year<=2024;year++) for(const month of [1,4,7,10]) {
    const start=`${year}-${String(month).padStart(2,'0')}-01`;
    const end=new Date(Date.UTC(year,month+2,0)).toISOString().slice(0,10);
    batches.push({region,start_date:start,end_date:end});
  }
  let weatherRows=0,vegetationRows=0;
  try {
    for(let index=0;index<batches.length;index++) {
      const batch=batches[index];
      status.textContent=`Backfilling ${index+1} of ${batches.length}: ${batch.region}, ${batch.start_date} to ${batch.end_date}. Completed batches are saved, so it is safe to retry after an interruption.`;
      const result=await api('/api/import/free-context',{method:'POST',headers:importHeaders('application/json'),body:JSON.stringify(batch)});
      weatherRows+=result.weather_rows;
      vegetationRows+=result.vegetation_rows;
    }
    status.textContent=`Free historical context is ready: ${formatter.format(weatherRows)} daily weather records and ${formatter.format(vegetationRows)} MODIS NDVI summaries were saved across all three study regions.`;
  } catch(e) {
    status.textContent=`Backfill paused: ${e.message} Completed batches remain saved. Retry to continue.`;
  } finally {button.disabled=false;}
}
async function loadIntegrationStatus() {
  try { renderProviderStatus(await api('/api/integrations/status')); }
  catch { $('weather-provider-status').textContent='Status unavailable'; }
}
function exportDetections() {
  try {
    const filters=dates();
    const query=urlParams({...filters,limit:runtimeLimits.max_export_rows});
    setImportMessage(`Preparing a filtered CSV export (up to ${formatter.format(runtimeLimits.max_export_rows)} rows)…`);
    window.location.assign('/api/export/detections.csv?'+query);
  } catch(err) { setImportMessage(err.message,true); }
}
function setLocalDateDefaults() {
  const now=new Date();
  const localToday=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10);
  const thirtyDaysAgo=new Date(now.getTime()-30*86400000-now.getTimezoneOffset()*60000).toISOString().slice(0,10);
  $('end').value=localToday;
  $('fetch-date').value=localToday;
  $('backfill-end').value=localToday;
  $('backfill-start').value=thirtyDaysAgo;
  $('context-end').value=localToday;
  $('context-start').value=new Date(now.getTime()-6*86400000-now.getTimezoneOffset()*60000).toISOString().slice(0,10);
}
async function init() {
  try {
    setLocalDateDefaults();
    regions=await api('/api/regions');
    $('region').innerHTML=regions.filter(x=>x.id!=='all').map(x=>`<option value="${safe(x.id)}">${safe(x.name)}</option>`).join('')+'<option value="all">All imported locations</option>';
    $('region').value='punjab';
    $('apply').addEventListener('click',refresh);
    $('upload-btn').addEventListener('click',uploadFile);
    $('fetch-btn').addEventListener('click',fetchFirms);
    $('backfill-btn').addEventListener('click',backfillFirms);
    $('availability-btn').addEventListener('click',checkAvailability);
    $('export-detections').addEventListener('click',exportDetections);
    $('explain-change').addEventListener('click',runChangeExplanation);
    $('export-evidence').addEventListener('click',exportEvidenceReport);
    $('load-weather').addEventListener('click',loadWeatherContext);
    $('load-vegetation').addEventListener('click',loadVegetationContext);
    $('backfill-free-context').addEventListener('click',backfillFreeContext);
    loadIntegrationStatus();
    $('grid-km').addEventListener('change',()=>{refreshHarmonization();refreshAnomalies();});
    $('viirs-source').addEventListener('change',refreshHarmonization);
    $('anomaly-sensor').addEventListener('change',refreshAnomalies);
    $('anomaly-analysis-mode').addEventListener('change',refreshAnomalies);
    $('anomaly-threshold').addEventListener('change',refreshAnomalies);
    $('anomaly-year').addEventListener('change',drawAnomalyYear);
    $('anomaly-export').addEventListener('click',exportAnomalyJSON);
    await refresh();
  } catch(err) { $('api-state').textContent='Backend offline';showError('Cannot connect to backend: '+err.message); }
}
init();


// v0.8.1: keep the section rail synchronized with the content currently in view.
(function setupSectionNavigation(){
  const links=[...document.querySelectorAll('.primary-nav .nav-link')];
  const targets=links.map(link=>document.querySelector(link.getAttribute('href'))).filter(Boolean);
  if(!('IntersectionObserver' in window)||!links.length)return;
  const linkByTarget=new Map(links.map(link=>[document.querySelector(link.getAttribute('href')),link]));
  const observer=new IntersectionObserver(entries=>{
    const visible=entries.filter(entry=>entry.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];
    if(!visible)return;
    links.forEach(link=>{const active=link===linkByTarget.get(visible.target);link.classList.toggle('active',active);if(active&&window.matchMedia('(max-width: 800px)').matches)link.scrollIntoView({block:'nearest',inline:'nearest'});});
  },{rootMargin:'-18% 0px -68% 0px',threshold:[0,.1,.25,.5]});
  targets.forEach(target=>observer.observe(target));
  links.forEach(link=>link.addEventListener('click',()=>{links.forEach(item=>item.classList.toggle('active',item===link));}));
})();
