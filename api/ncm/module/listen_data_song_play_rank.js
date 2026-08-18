// 听歌足迹 - 歌曲播放排行 (Top20)
const createOption = require('../util/option.js')
module.exports = (query, request) => {
  return request(
    `/api/content/activity/listen/data/song/play/rank`,
    {
      type: query.type || 'month', //周 week 月 month
      endTime: query.endTime, // 不填就是本周/月的
    },
    createOption(query),
  )
}
