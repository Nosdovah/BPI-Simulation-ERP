import { useEffect, useState } from 'react'

const TABS = ['Overview', 'Orders', 'Webhook Events', 'Bundles']
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
  const [err, setErr] = useState(null)

  useEffect(() => {
    get('/v1/open/admin/summary').then(setSummary).catch((e) => setErr(String(e)))
  }, [])

  async function loadOrders(status) {
    setOrderStatus(status)
    const q = status ? `?status=${status}` : ''
    return get('/v1/open/sales-order/list' + q).then(setOrders).catch((e) => setErr(String(e)))
  }
  async function loadEvents() {
    return get('/v1/open/admin/webhook-events').then(setEvents).catch((e) => setErr(String(e)))
  }
  async function loadBundles() {
    return get('/v1/open/admin/bundles').then(setBundles).catch((e) => setErr(String(e)))
  }

  useEffect(() => { if (tab === 'Orders') loadOrders(orderStatus) }, [tab])
  useEffect(() => { if (tab === 'Webhook Events') loadEvents() }, [tab])
  useEffect(() => { if (tab === 'Bundles') loadBundles() }, [tab])

  const comp = (sku) => (summary?.focal_bundle?.components || []).find((c) => c.component_sku === sku)

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
          <Card label="Mode" value="to-be" tone="ok" />
          <Card label="SKU variants" value={summary.catalog.variants.toLocaleString('id-ID')} />
          <Card label="Bundle (is_bundle)" value={summary.catalog.bundles} />
          <Card label="Relasi BOM (sku_bom)" value={summary.catalog.bom_rows} tone="ok" />
          <Card label="Stok bundle (BOM)" value={summary.focal_bundle.available_stock.toLocaleString('id-ID')} tone="ok" />
          <Card label="Komponen M2291" value={comp('M2291')?.stock_available.toLocaleString('id-ID') ?? '—'} />
          <Card label="Komponen M2292" value={comp('M2292')?.stock_available.toLocaleString('id-ID') ?? '—'} />
          <Card label="Order total" value={summary.orders.total} />
          <Card label="Webhook terkirim (HMAC)" value={summary.webhook.delivered} tone="ok" />
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
        <table>
          <thead><tr><th>event</th><th>event_id</th><th>HTTP</th><th>signature (HMAC)</th><th>sent_at</th></tr></thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td>{e.event}</td>
                <td>{e.event_id}</td>
                <td>{e.http_status}</td>
                <td><code>{e.signature}</code></td>
                <td>{e.sent_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {tab === 'Bundles' && (
        <table>
          <thead><tr><th>bundle_sku</th><th>is_bundle</th><th>BOM rows</th><th>stok (BOM)</th><th>komponen</th></tr></thead>
          <tbody>
            {bundles.map((b) => (
              <tr key={b.bundle_sku}>
                <td>{b.bundle_sku}</td>
                <td>{b.is_bundle ? '✅' : '❌'}</td>
                <td>{b.bom_rows}</td>
                <td>{b.available_stock?.toLocaleString('id-ID')}</td>
                <td>{b.components.map((c) => `${c.component_sku}×${c.multiplier}`).join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}