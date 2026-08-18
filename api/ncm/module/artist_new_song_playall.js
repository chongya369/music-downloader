// 获取所有关注歌手最近的 50 首新歌

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  return request(
    `/api/sub/artist/new/works/song/playall`,
    {},
    createOption(query, 'eapi'),
  )
}
