// 获取关注歌手的新歌曲和 MV

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    startTimestamp: query.startTimestamp || query.before || Date.now(),
    sourceType: query.sourceType || 1,
    limit: query.limit || 10,
    firstRequest: query.firstRequest ?? true,
  }
  return request(
    `/api/sub/artist/new/works/song-mv/list/v2`,
    data,
    createOption(query, 'eapi'),
  )
}
