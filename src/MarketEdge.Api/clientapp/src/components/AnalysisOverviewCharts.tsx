import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line,
} from 'recharts';

interface AnalysisOverviewChartsProps {
  stage: 2 | 4;
  sectors: { name: string; count: number }[];
  history: { date: string; count: number }[];
}

export function AnalysisOverviewCharts({ stage, sectors, history }: AnalysisOverviewChartsProps) {
  const tooltipStyle = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8 };
  return <div className="analysis-grid">
    <div className="chart-card">
      <h3 className="chart-title">Stage {stage} Stocks by Sector (Top 20)</h3>
      <ResponsiveContainer width="100%" height={400}>
        <BarChart data={sectors} layout="vertical" margin={{ left: 120, right: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis type="number" stroke="var(--text-muted)" />
          <YAxis dataKey="name" type="category" width={120} tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
          <Tooltip contentStyle={tooltipStyle} />
          <Bar dataKey="count" fill="var(--primary)" name={`Stage ${stage}`} radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    {history.length > 1 && <div className="chart-card">
      <h3 className="chart-title">Stage {stage} Count Over Time</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={history}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="date" stroke="var(--text-muted)" tick={{ fontSize: 11 }} />
          <YAxis stroke="var(--text-muted)" />
          <Tooltip contentStyle={tooltipStyle} />
          <Line type="monotone" dataKey="count" name={`stage${stage}`} stroke="var(--primary)" strokeWidth={2} dot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>}
  </div>;
}
