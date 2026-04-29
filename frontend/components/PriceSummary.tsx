import type { Quote } from '@/types'

interface Props {
  quote: Quote
}

export function PriceSummary({ quote }: Props) {
  const positive = quote.change >= 0
  const sign = positive ? '+' : ''

  return (
    <div className="flex items-baseline gap-2.5">
      <span className="text-xl font-bold tabular-nums text-gray-900 dark:text-gray-100 font-mono">
        {quote.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
      </span>
      <span className={`text-sm tabular-nums font-mono px-2 py-0.5 rounded ${
        positive
          ? 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-400/10'
          : 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-400/10'
      }`}>
        {sign}{quote.change.toFixed(2)}&nbsp;({sign}{quote.change_percent.toFixed(2)}%)
      </span>
    </div>
  )
}
