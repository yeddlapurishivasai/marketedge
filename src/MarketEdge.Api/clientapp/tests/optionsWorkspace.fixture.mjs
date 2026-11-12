// Synthetic immutable API-shaped evidence; never submitted as a forecast or order.
export function createOptionsFixture(mode = 'daily') {
  const asOf = '2026-11-10T01:50:00Z', observed = '2026-11-09T12:05:06Z';
  const targetAt = mode === 'weekly' ? '2026-11-17T10:00:00Z' : '2026-11-11T10:00:00Z';
  const metric = (key, label, value, unit, direction, contribution, extra = {}) => ({
    key, label, value, unit, direction, contribution, source: 'Synthetic captured evidence',
    asOf: observed, ...extra,
  });
  const leg = (direction, optionType, strike, entryPrice) => ({
    direction, optionType, strike, entryPrice, quantity: 75, exchange: 'NFO', instrumentToken: strike,
    tradingSymbol: `FIXTURE-${strike}-${optionType}`, quoteObservedAt: observed, priceBasis: 'captured-bid-ask',
  });
  const base = {
    riskTier: 'high', expiry: '2026-11-17', lotSize: 75, netPremium: 3000,
    maxProfit: null, maxLoss: null, grossMaxProfit: 3000, grossMaxLoss: 4500, costEstimate: null,
    capitalBasis: null, rewardRisk: null, probabilityOfProfit: null, probabilityType: 'unavailable',
    scenarioIds: ['base', 'wide'], executionStatus: 'planning-only', requiresLiveRevalidation: true,
    quoteAsOf: observed, plannedExitAt: targetAt, grossTargetProfit: 1500,
    targetProfit: null, targetPremium: 20, estimatedStopLoss: null,
    riskTierReasons: ['High uncertainty: charges and model inputs unavailable.'],
    technicalStop: { lower: 24750, upper: 25250, trigger: 'first index quote at/beyond level' },
    warnings: ['Reference premiums only; live bid/ask revalidation required.'],
  };
  const candidates = [
    { ...base, id: 'fixture-sell-condor', side: 'selling', strategy: 'iron-condor',
      legs: [leg('sell', 'PE', 24800, 30), leg('buy', 'PE', 24700, 10), leg('sell', 'CE', 25200, 30), leg('buy', 'CE', 25300, 10)] },
    { ...base, id: 'fixture-sell-put', side: 'selling', strategy: 'put-credit-spread',
      technicalStop: { lower: 24750, upper: null, trigger: 'first index quote at/beyond level' },
      legs: [leg('sell', 'PE', 24800, 80), leg('buy', 'PE', 24700, 40)] },
    { ...base, id: 'fixture-buy-call', side: 'buying', strategy: 'long-call', netPremium: -3750,
      grossMaxLoss: 3750, grossMaxProfit: null, targetPremium: 75,
      legs: [leg('buy', 'CE', 25000, 50)] },
    { ...base, id: 'fixture-buy-spread', side: 'buying', strategy: 'call-debit-spread', netPremium: -3000,
      grossMaxLoss: 3000, grossMaxProfit: 4500,
      legs: [leg('buy', 'CE', 25000, 80), leg('sell', 'CE', 25100, 40)] },
  ];
  const macro = { key: 'macro', label: 'Macro/events', score: 50, coverage: 0.25, scale: 'Oil context only',
    reason: 'Oil and cash flows are context, not calibrated index-direction forecasts.',
    indicators: [
      metric('crude-change', 'MCX crude change', -0.06, '%', 'supportive', 0.03, { contextOnly: true, contributionUnit: 'oil-context score points' }),
      metric('fii-cash-net', 'FII cash net', -500, 'INR crore', 'context', null, { contextOnly: true, asOf: '2026-11-09', sessionDate: '2026-11-09' }),
      metric('dii-cash-net', 'DII cash net', 700, 'INR crore', 'context', null, { contextOnly: true, asOf: '2026-11-09', sessionDate: '2026-11-09' }),
    ] };
  return {
    id: 993001, indexCode: 'NIFTY50', mode, status: 'completed', generatedAt: asOf, targetAt,
    ai: { status: 'completed', selection: {
      scenarioId: 'base', scenarioReason: 'The baseline balances captured volatility and partial structural evidence.',
      recommendations: ['selling', 'buying'].flatMap(side => ['low', 'medium', 'high'].map(riskTier => ({
        side, riskTier, candidateId: riskTier === 'high' ? (side === 'selling' ? candidates[0].id : candidates[2].id) : null,
        reason: riskTier === 'high' ? 'Planning watchlist only; revalidate live prices and risk.' : 'No eligible supported candidate.',
      }))),
    } },
    analysis: {
      schemaVersion: 'options.v1', modelVersion: 'options-european-v1', configVersion: 'options-rules-v4',
      spot: 25000, spotObservedAt: observed, asOf, bias: 'neutral',
      executionStatus: 'planning-only', quoteAsOf: observed, plannedExitAt: targetAt,
      modelInputs: { riskFreeRate: null, dividendYield: null, roundTripCostPerLeg: null },
      snapshotPolicy: { executionStatus: 'planning-only', quoteAsOf: observed, sessionDate: '2026-11-09',
        reason: 'Dated reference observation, not an official closing print or an entry.' },
      levels: { support: 24750, resistance: 25250 }, candidates, warnings: ['Rate/dividend yield unavailable: no model metrics.'],
      scenarios: [
        { id: 'narrow', label: 'Narrow', low: 24869.09, high: 25221.45 },
        { id: 'base', label: 'Baseline', low: 24712.13, high: 25381.65 },
        { id: 'wide', label: 'Wide', low: 24537.89, high: 25561.88 },
      ].map(value => ({ ...value, probability: null, probabilityType: 'unavailable', targetAt })),
      dimensions: [
        { key: 'technicals', label: 'Technicals', score: 60, coverage: 1, scale: 'Bearish to bullish', indicators: [
          metric('sma20', 'SMA 20', 24900, 'points', 'bullish', 1),
          metric('sma50', 'SMA 50', 25100, 'points', 'bearish', -1),
          metric('sma200', 'SMA 200', 24000, 'points', 'bullish', 1),
          metric('ema20', 'EMA 20', 24950, 'points', 'neutral', null),
          metric('rsi14', 'RSI 14', 62, 'oscillator', 'bullish', 1),
          metric('macd', 'MACD 12/26', 12, 'points', 'bullish', 1),
          metric('macdSignal', 'MACD signal 9', 10, 'points', 'neutral', null),
          metric('adx14', 'ADX 14', 23, 'strength', 'neutral', null),
        ] },
        { key: 'volatility', label: 'Volatility', score: 32, coverage: 1, scale: 'Low to high', indicators: [
          metric('india-vix', 'India VIX', 14.3, '%', 'context', 0),
          metric('realizedVol20', '20-session realised volatility', 0.16, 'annualised fraction', 'context', null),
          metric('atr14', 'ATR 14', 150, 'points', 'context', null),
        ] },
        { key: 'derivatives', label: 'Derivatives positioning', score: 50, coverage: 1, scale: 'Context only', indicators: [
          metric('window-oi-pcr', 'OI put/call ratio', 0.95, 'ratio', 'context', 0),
          metric('basis-2026-11-17', 'Futures basis November', 12, 'points', 'context', 0),
          metric('basis-2026-12-29', 'Futures basis December', 8.5, 'points', 'context', 0),
        ] },
        { key: 'liquidity', label: 'Reference book coverage', score: 45, coverage: 1, scale: 'Coverage only',
          indicators: [{ key: 'validQuotes', label: 'Retained references', value: 100, unit: 'contracts' },
            { key: 'validBooks', label: 'Usable bid/ask books', value: 45, unit: 'contracts' }] },
        macro,
      ],
      fundamentalRange: { status: 'partial', low: 22850, base: 25000, high: 27150, coverage: 0.86,
        aiCoverage: 0.02, analystCoverage: 0.78, mathematicalCoverage: 0.06, uncoveredWeightPct: 14,
        horizon: 'structural-not-run-target', sensitivityOnly: true, reason: 'Uncovered stocks held at captured price.' },
      constituents: Array.from({ length: 50 }, (_, i) => ({
        symbol: `FIXTURE${i + 1}`, companyName: `Synthetic company ${i + 1}`, weightPct: 2,
        livePrice: 100, fairLow: i < 43 ? 90 : null, fairBase: i < 43 ? 100 : null, fairHigh: i < 43 ? 110 : null,
        latestQuarterEnd: '2026-09-30', valuationAsOf: '2026-11-09',
        method: i === 0 ? 'stored-stock-AI-valuation' : i < 40 ? 'stored-analyst-12m-targets' : i < 43 ? 'trailing-EPS/sector-peer-PE' : 'unavailable',
        weightSource: 'auto:nippon-niftybees-etf-holdings-proxy', weightAsOf: '2026-10-31',
        aiPriceForecasts: i === 0 ? [{ horizon: '1y', low: 90, base: 100, high: 110 }] : [],
      })),
      referenceData: { weights: { sourceType: 'etf-holdings-proxy', sourceDate: '2026-10-31' } },
      marketContext: { dimension: macro,
        news: Array.from({ length: 5 }, (_, i) => ({ title: `Synthetic dated headline ${i + 1}`, url: `https://example.invalid/${i}`,
          publishedAt: '2026-11-09T10:00:00Z', source: 'Synthetic feed', untrustedText: true })),
        evidence: { crude: { name: 'MCX crude futures', price: 8224, previousClose: 8229, changePct: -0.06,
          unit: 'INR/barrel', asOf: observed, source: 'Synthetic captured quote', quoteType: 'delayed-observation' },
        flows: { status: 'available', sessionDate: '2026-11-09', asOf: '2026-11-09', unit: 'INR crore', source: 'Synthetic official-format evidence',
          fii: { buy: 2500, sell: 3000, net: -500 }, dii: { buy: 3000, sell: 2300, net: 700 } } } },
    },
  };
}
