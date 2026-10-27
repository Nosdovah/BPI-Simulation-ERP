import { useEffect, useState } from 'react'

const TABS = ['Overview', 'Orders', 'Webhook Events', 'Bundles', 'Replay']
const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

async function get(path) {
  const r = await fetch(API_BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  const j = await r.json()
  return j.data
}

function Card({ label, value, tone }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className={'card-value ' + (tone || '')}>{value}</div>
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState('Overview')
  const [summary, setSummary] = useState(null)
  const [orders, setOrders] = useState([])
  const [events, setEvents] = useState([])
  const [bundles, setBundles] = useState([])
  const [orderStatus, setOrderStatus] = useState('')
  const [eventLane, setEventLane] = useState('')
  const [replayMsg, setReplayMsg] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    get('/v1/open/admin/summary').then(setSummary).catch((e) => setErr(String(e)))
  }, [])

  async function loadOrders(status) {
    setOrderStatus(status)
    const q = status ? `?status=${status}` : ''
    return get('/v1/open/sales-order/list' + q).then(setOrders).catch((e) => setErr(String(e)))
  }
  async function loadEvents(lane) {
    setEventLane(lane)
    const q = lane ? `?lane=${lane}` : ''
    return get('/v1/open/admin/webhook-events' + q).then(setEvents).catch((e) => setErr(String(e)))
  }
  async function loadBundles() {
    return get('/v1/open/admin/bundles').then(setBundles).catch((e) => setErr(String(e)))
  }
  async function replay() {
    setReplayMsg('mengirim…')
    try {
      const r = await fetch(API_BASE + '/v1/open/catalog/push', { method: 'POST' })
      const j = await r.json()
      setReplayMsg(`${j.data.products} produk terkirim (upstream ${j.data.upstream_status})`)
    } catch (e) {
      setReplayMsg('gagal: ' + e)
    }
  }

  useEffect(() => { if (tab === 'Orders') loadOrders(orderStatus) }, [tab])
  useEffect(() => { if (tab === 'Webhook Events') loadEvents(eventLane) }, [tab])
  useEffect(() => { if (tab === 'Bundles') loadBundles() }, [tab])

  const phantom = summary
    ? (summary.bundle.component_m2291 ?? 0) - (summary.bundle.component_m2292 ?? 0)
    : null

  return (
    <div className="page">
      <header>
        <h1>OSR Admin</h1>
        <span className="mode">mode: {summary ? summary.mode : '—'}</span>
      </header>

      <nav>
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      {err && <p className="err">Error: {err}</p>}

      {tab === 'Overview' && summary && (
        <div className="grid">
          <Card label="Order total" value={summary.orders.total} />
          <Card label="Macet 'sent'" value={`${summary.orders.stuck_sent} (${summary.orders.stuck_pct}%)`} tone="warn" />
          <Card label="Macet >2 hari" value={summary.orders.stuck_gt2d} tone="warn" />
          <Card label="Completed manual" value={`${summary.orders.manual}/${summary.orders.completed}`} />
          <Card label="Webhook valid (4.2)" value={`${summary.webhook_4_2.valid}/${summary.webhook_4_2.events} (${summary.webhook_4_2.validity_pct}%)`} tone="warn" />
          <Card label="DPMO" value={summary.webhook_4_2.dpmo.toLocaleString('id-ID')} tone="warn" />
          <Card label="Bundle quota statis" value={summary.bundle.quota?.toLocaleString('id-ID')} tone="warn" />
          <Card label="BOM rows" value={summary.bundle.bundle_components_rows} tone="warn" />
          <Card label="Phantom (M2291−M2292)" value={phantom?.toLocaleString('id-ID') + ' unit'} tone="warn" />
        </div>
      )}

      {tab === 'Orders' && (
        <>
          <div className="filters">
            {['', 'paid', 'process', 'sent', 'completed', 'cancelled'].map((s) => (
              <button key={s || 'all'} className={orderStatus === s ? 'active' : ''}
                onClick={() => loadOrders(s)}>{s || 'Semua'}</button>
            ))}
          </div>
          <table>
            <thead><tr><th>order_ref</th><th>status</th><th>actor</th><th>courier</th><th>tracking</th></tr></thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td>{o.order_ref}</td>
                  <td><span className={'st ' + o.status}>{o.status}</span></td>
                  <td>{o.actor_last}</td>
                  <td>{o.courier_name}</td>
                  <td>{o.tracking_number}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tab === 'Webhook Events' && (
        <>
          <div className="filters">
            {['', '4.1', '4.2'].map((l) => (
              <button key={l || 'all'} className={eventLane === l ? 'active' : ''}
                onClick={() => loadEvents(l)}>{l || 'Semua lane'}</button>
            ))}
          </div>
          <table>
            <thead><tr><th>event_id</th><th>lane</th><th>valid</th><th>rejected</th><th>reason</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event_id}>
                  <td>{e.event_id}</td>
                  <td>{e.lane}</td>
                  <td>{e.payload_valid ? '✅' : '❌'}</td>
                  <td>{e.mapper_rejected ? '🚫' : ''}</td>
                  <td>{e.reject_reason || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tab === 'Bundles' && (
        <table>
          <thead><tr><th>bundle_sku</th><th>is_bundle</th><th>quota statis</th><th>BOM rows</th><th>stok</th><th>set_by</th></tr></thead>
          <tbody>
            {bundles.map((b) => (
              <tr key={b.bundle_sku}>
                <td>{b.bundle_sku}</td>
                <td>{b.is_bundle ? '✅' : '❌'}</td>
                <td>{b.quota_static?.toLocaleString('id-ID')}</td>
                <td>{b.bundle_components_rows}</td>
                <td>{b.stock_available?.toLocaleString('id-ID')}</td>
                <td>{b.set_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {tab === 'Replay' && (
        <div className="replay">
          <p>Kirim ulang katalog Jalur A (webhook <code>catalog.updated</code>) ke core/storefront.</p>
          <button onClick={replay}>Re-push katalog</button>
          {replayMsg && <p className="replay-msg">{replayMsg}</p>}
        </div>
      )}
    </div>
  )
}