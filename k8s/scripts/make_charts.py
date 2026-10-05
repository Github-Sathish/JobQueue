"""Regenerates the README charts from the raw run data. Run from the repository root: python k8s/scripts/make_charts.py"""
import pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MultipleLocator

import os
OUT = 'k8s/charts/'
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                     'axes.grid': True, 'grid.alpha': 0.25, 'figure.dpi': 150})
C = {'users100': '#c0392b', 'hpa-cpu': '#e67e22', 'workers8': '#2471a3', 'kill-worker2': '#7d3c98', 'kill-worker3': '#117a65'}

j = pd.read_csv('k8s/results/jobs_export.csv')
for c in ['created_at', 'started_at', 'completed_at']:
    j[c] = pd.to_datetime(j[c], utc=True)
ri = pd.read_csv('k8s/results/run_index.csv')
ri['start'] = pd.to_datetime(ri['start_utc'], utc=True)

def run_jobs(name):
    r = ri[ri.run == name].iloc[0]
    sub = j[(j.created_at >= r.start - pd.Timedelta(seconds=75)) & (j.created_at <= r.start + pd.Timedelta(seconds=200))]
    return sub

def unfinished_series(sub, t0, t_end_s, step=1):
    t = np.arange(0, t_end_s, step)
    created = ((sub.created_at - t0).dt.total_seconds()).values
    done = ((sub.completed_at - t0).dt.total_seconds()).values
    done = done[~np.isnan(done)]
    cs = np.sort(created); ds = np.sort(done)
    u = np.searchsorted(cs, t, side='right') - np.searchsorted(ds, t, side='right')
    return t, u

mins = FuncFormatter(lambda x, p: f'{x/60:g}')
def minute_axis(ax, step_min):
    ax.xaxis.set_major_locator(MultipleLocator(step_min*60)); ax.xaxis.set_major_formatter(mins)

# ---- Chart 1: unfinished jobs, 1 worker vs 1 worker + HPA vs 8 workers
fig, ax = plt.subplots(figsize=(9, 4.8))
labels = {'users100': '1 worker pod', 'hpa-cpu': '1 worker pod + CPU autoscaler (never scaled)', 'workers8': '8 worker pods'}
stats = {}
for name in ['users100', 'hpa-cpu', 'workers8']:
    sub = run_jobs(name); t0 = sub.created_at.min()
    t, u = unfinished_series(sub, t0, 15*60)
    ax.plot(t, u, color=C[name], lw=2, label=labels[name])
    stats[name] = (u.max(), t[u.argmax()])
ax.axvline(120, color='grey', ls='--', lw=1); ax.text(128, 650, 'load ends (2 min)', color='grey', fontsize=9)
minute_axis(ax, 2); ax.set_xlabel('Minutes since the first job was created'); ax.set_ylabel('Unfinished jobs (pending + processing)')
ax.set_title('100 users: API stayed healthy, but one worker pod could not keep up')
ax.legend(frameon=False, loc='upper right', bbox_to_anchor=(1, 0.88))
fig.tight_layout(); fig.savefig(OUT + '1_backlog.png'); plt.close(fig)
print('chart1', stats)

# ---- Chart 2: autoscaler blind spot
h = pd.read_csv('k8s/results/hpa-cpu/hpa.csv', header=None, names=['ts', 'name', 'x', 'tgt', 'min', 'max'])
h['ts'] = pd.to_datetime(h['ts'], utc=True)
h['util'] = h['tgt'].str.split('/').str[0].str.rstrip('%').astype(float)
h['target'] = h['tgt'].str.split('/').str[1].str.rstrip('%').astype(float)
sub = run_jobs('hpa-cpu'); t0 = sub.created_at.min()
h['t'] = (h['ts'] - t0).dt.total_seconds()
rp = pd.read_csv('k8s/results/hpa-cpu/replicas.csv', header=None, names=['ts', 'name', 'ready'])
rp = rp[rp.name == 'worker']
print('worker replica readings:', rp.ready.value_counts().to_dict())
fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 6.2), sharex=True, gridspec_kw={'height_ratios': [1, 1]})
a1.plot(h['t'], h['util'], color=C['hpa-cpu'], lw=2, label='Worker CPU (% of request), as seen by the autoscaler')
a1.axhline(50, color='black', ls='--', lw=1.2, label='Scale-up target (50%)')
a1.set_ylim(0, 60); a1.set_ylabel('CPU utilization (%)')
a1.legend(frameon=False, loc='center right', bbox_to_anchor=(1, 0.62))
a1.set_title(f'CPU autoscaler never scaled: replicas stayed at 1/1 in all {len(rp)} samples')
t, u = unfinished_series(sub, t0, 14*60)
a2.plot(t, u, color=C['users100'], lw=2); a2.set_ylabel('Unfinished jobs')
a2.axvline(120, color='grey', ls='--', lw=1); a2.text(128, 600, 'load ends (2 min)', color='grey', fontsize=9)
minute_axis(a2, 2); a2.set_xlabel('Minutes since the first job was created')
a2.set_title('...while the backlog grew past 1,400 jobs', fontsize=10)
fig.tight_layout(); fig.savefig(OUT + '2_autoscaler.png'); plt.close(fig)
print('chart2 util max', h.util.max(), 'min', h.util.min(), 'peak unfinished', u.max())

# ---- Chart 3: DB connections vs max_connections
fig, ax = plt.subplots(figsize=(9, 4.6))
lab3 = {'users100': '1 worker pod', 'workers8': '8 worker pods', 'kill-worker3': '8 worker pods, one killed at 60 s'}
peaks = {}
for name in ['users100', 'workers8', 'kill-worker3']:
    sub = run_jobs(name); t0 = sub.created_at.min()
    c = pd.read_csv(f'k8s/results/{name}/conn.csv'); c.columns = [x.strip() for x in c.columns]
    c['ts'] = pd.to_datetime(c['timestamp'].str.strip()).dt.tz_localize('Asia/Kolkata').dt.tz_convert('UTC')
    c['t'] = (c['ts'] - t0).dt.total_seconds()
    c = c[(c.t >= -10) & (c.t <= 3.5*60)]
    ax.plot(c['t'], c['total_connections'], color=C[name], lw=1.4, label=lab3[name])
    peaks[name] = int(c.total_connections.max())
ax.axhline(100, color='black', ls='--', lw=1.2); ax.text(8, 102, 'PostgreSQL max_connections = 100', fontsize=9)
ax.axvline(120, color='grey', ls='--', lw=1); ax.text(124, 62, 'load ends', color='grey', fontsize=9)
ax.set_ylim(0, 112); minute_axis(ax, 0.5)
ax.set_xlabel('Minutes since the first job was created'); ax.set_ylabel('Open PostgreSQL connections')
ax.set_title('Connections grew with worker count but stayed well below the limit')
ax.legend(frameon=False, loc='center right', bbox_to_anchor=(1, 0.55))
fig.tight_layout(); fig.savefig(OUT + '3_connections.png'); plt.close(fig)
print('chart3 peaks', peaks)

# ---- Chart 4: jobs stranded by a mid-load worker kill
kills = {'kill-worker2': pd.Timestamp('2026-10-04T15:29:05Z'), 'kill-worker3': pd.Timestamp('2026-10-04T17:04:04Z')}
lab4 = {'kill-worker2': 'Run A (kill at 20:59:05 IST)', 'kill-worker3': 'Run B (kill at 22:34:04 IST)'}
fig, (a1, a2) = plt.subplots(1, 2, figsize=(11.5, 4.8))
res = {}
for name, k in kills.items():
    sub = run_jobs(name)
    t = np.arange(-60, 78*60, 1)
    created = ((sub.created_at - k).dt.total_seconds()).values
    done = ((sub.completed_at - k).dt.total_seconds()).values; done = done[~np.isnan(done)]
    u = np.searchsorted(np.sort(created), t, side='right') - np.searchsorted(np.sort(done), t, side='right')
    for ax in (a1, a2):
        ax.plot(t, u, color=C[name], lw=2, label=lab4[name])
    res[name] = dict(at15=int(u[t == 15*60][0]), at5=int(u[t == 5*60][0]), end=int(u[-1]), max=int(u.max()))
a1.set_xlim(-60, 15*60); a1.set_ylim(0, 120); minute_axis(a1, 3)
a2.set_xlim(4*60, 78*60); a2.set_ylim(0, 10); minute_axis(a2, 10)
for ax in (a1, a2):
    ax.axvline(0, color='black', ls='--', lw=1.2)
    ax.set_xlabel('Minutes since the worker pod was killed'); ax.set_ylabel('Unfinished jobs')
a1.text(8, 113, 'pod killed', fontsize=9)
a1.set_title('First 15 minutes: the drain stalls at 7 to 8 jobs instead of reaching 0', fontsize=10)
a2.set_title('Full hour: most stuck jobs return at about 60 min, a few never do', fontsize=10)
a1.legend(frameon=False, loc='upper right')
a2.annotate('Run A: 8 stuck, 5 return, 3 stay stuck', xy=(60*60, 3), xytext=(22*60, 5.2), fontsize=9, color=C['kill-worker2'], arrowprops=dict(arrowstyle='->', color=C['kill-worker2']))
a2.annotate('Run B: 7 stuck, 6 return, 1 stays stuck', xy=(61*60, 1), xytext=(22*60, 2.2), fontsize=9, color=C['kill-worker3'], arrowprops=dict(arrowstyle='->', color=C['kill-worker3']))
fig.suptitle('Killing 1 of 8 worker pods mid-load stranded jobs without any error signal', fontsize=11)
fig.tight_layout(); fig.savefig(OUT + '4_worker_kill.png'); plt.close(fig)
print('chart4', res)
