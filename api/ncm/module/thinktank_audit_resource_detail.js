// 云小编获取任务
//
// type:
// 1: 歌曲曲风审核 musicalStyleEnter
// 2: 歌曲语种审核 languageEnter
// 3: 歌曲原唱审核 oriSingerEnter
// 4: 情绪标签审核 emotionEnter

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  const data = {
    type: query.type || '4',
  }
  return request(
    `/api/thinktank/audit/resource/detail`,
    data,
    createOption(query, 'eapi'),
  )
}
