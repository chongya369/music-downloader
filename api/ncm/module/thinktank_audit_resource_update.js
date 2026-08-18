// 云小编提交任务
//
// type:
// 1: 歌曲曲风审核 musicalStyleEnter
// 2: 歌曲语种审核 languageEnter
// 3: 歌曲原唱审核 oriSingerEnter
// 4: 情绪标签审核 emotionEnter
//
// taskId:
// 调用 thinktank/audit/resource/detail 获取
//
// judgement:
// 1: 同意, 2: 否决, 3: 跳过 (不算次数)

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  if (!query.taskId || !query.judgement)
    return Promise.reject({
      status: 400,
      body: {
        code: 400,
        message: '参数不足',
      },
    })
  const data = {
    type: query.type || '4',
    taskId: query.taskId,
    judgement: query.judgement,
  }
  return request(
    `/api/thinktank/audit/resource/update`,
    data,
    createOption(query, 'eapi'),
  )
}
