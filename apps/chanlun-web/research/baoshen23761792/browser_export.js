/* =============================================================
 * X/Twitter 搜索页 → 自动滚动收集推文 → 导出 CSV
 * 目标账号: @baoshen23761792 (BlackJack)
 *
 * 用法:
 *   1) 用浏览器登录 X (需要能正常访问 x.com 的网络环境)
 *   2) 打开搜索页(Latest 标签, 即 URL 带 &f=live):
 *      https://x.com/search?q=from%3Abaoshen23761792%20since%3A2026-01-01%20until%3A2026-09-22&f=live
 *      说明: until 不含当天, 想看 9/21 就写 09-22;
 *            排除回复加 %20-filter%3Areplies, 只看回复用 %20filter%3Areplies
 *   3) F12 → Console → 粘贴本文件全部内容 → 回车
 *   4) 保持该标签页在前台, 等自动滚动结束, 会自动下载 CSV
 *   5) 若要喂给 collect_public.py, 在 Console 再执行:
 *        copy(window.__bsTweetIds())
 *      然后追加到 research/baoshen23761792/tweet_ids.txt
 *
 * 注意:
 *   - from: 搜索默认不含转推; 要含转推需在 q 里加 %20include%3Anativeretweets,
 *     但转推的链接作者不是本人, 本脚本按作者过滤会跳过它们。
 *   - 搜索页是虚拟列表, 脚本边滚边收, 不要中途刷新/切走标签页。
 *   - 遇到速率限制会自动暂停 60 秒; 如被中断, 缩小时间范围分月重跑即可。
 * ============================================================= */
(async () => {
  const HANDLE = 'baoshen23761792';
  const MAX_ROUNDS = 400;   // 最多滚动轮数
  const IDLE_ROUNDS = 12;   // 连续多少轮无新推文则停止
  const DELAY_MS = 1200;    // 滚动间隔基准(毫秒), 实际会加随机抖动

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const tweets = new Map();
  let idle = 0;
  let rounds = 0;

  function parseCount(s) {
    if (!s) return null;
    s = s.trim().replace(/,/g, '');
    const m = s.match(/^([\d.]+)\s*([KkMm万萬亿億]?)$/);
    if (!m) return null;
    let n = parseFloat(m[1]);
    const u = m[2];
    if (u === 'K' || u === 'k') n *= 1e3;
    if (u === 'M' || u === 'm') n *= 1e6;
    if (u === '万') n *= 1e4;
    if (u === '萬') n *= 1e4;
    if (u === '亿' || u === '億') n *= 1e8;
    return Math.round(n);
  }

  function metric(article, testid) {
    const el = article.querySelector('[data-testid="' + testid + '"]');
    return el ? parseCount(el.innerText) : null;
  }

  function grab() {
    let added = 0;
    document.querySelectorAll('article').forEach((article) => {
      const link = article.querySelector('a[href*="/status/"]');
      const time = article.querySelector('time[datetime]');
      if (!link || !time) return;
      const m = (link.getAttribute('href') || '').match(/(?:x\.com\/)?([^/]+)\/status\/(\d+)/);
      if (!m || m[1].toLowerCase() !== HANDLE) return;
      const id = m[2];
      if (tweets.has(id)) return;
      const textEl = article.querySelector('div[data-testid="tweetText"]');
      const head = article.innerText.slice(0, 60);
      tweets.set(id, {
        id: id,
        datetime: time.getAttribute('datetime'),
        text: textEl ? textEl.innerText : '',
        is_reply: /(回复|回覆|Replying to|返信)/.test(head) ? 1 : 0,
        likes: metric(article, 'like'),
        retweets: metric(article, 'retweet'),
        replies: metric(article, 'reply'),
        url: 'https://x.com/' + HANDLE + '/status/' + id,
      });
      added++;
    });
    return added;
  }

  console.log('开始收集 @' + HANDLE + ' ...');
  while (rounds < MAX_ROUNDS && idle < IDLE_ROUNDS) {
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(DELAY_MS + Math.random() * 700);
    const before = tweets.size;
    grab();
    idle = tweets.size === before ? idle + 1 : 0;
    rounds++;
    if (rounds % 10 === 0) console.log('已收集 ' + tweets.size + ' 条 (第 ' + rounds + ' 轮)');
    if (rounds % 5 === 0 && /(rate limit|速率上限|超出上限|请稍后再试)/i.test(document.body.innerText)) {
      console.warn('检测到速率限制, 等待 60 秒...');
      await sleep(60000);
    }
  }

  const rows = [...tweets.values()].sort((a, b) => (a.datetime < b.datetime ? -1 : 1));
  window.__bsTweets = rows;
  window.__bsTweetIds = () => rows.map((t) => t.id).join('\n');

  if (!rows.length) {
    console.warn('没有收集到推文: 请确认已登录、URL 带 f=live、且时间范围内有内容。');
    return;
  }

  const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
  const header = ['id', 'datetime', 'text', 'is_reply', 'likes', 'retweets', 'replies', 'url'];
  const csv = '\ufeff' + header.join(',') + '\n'
    + rows.map((r) => header.map((h) => esc(r[h])).join(',')).join('\n');

  const name = 'x_' + HANDLE + '_' + rows[0].datetime.slice(0, 10) + '_' + rows[rows.length - 1].datetime.slice(0, 10) + '.csv';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();

  console.log('完成: ' + rows.length + ' 条, 已下载 ' + name);
  console.log('导出 ID 清单: copy(window.__bsTweetIds())  → 追加到 tweet_ids.txt');
})();
