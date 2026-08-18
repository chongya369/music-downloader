// 云小编考试开始
//
// examType:
// 1. 歌曲曲风审核: musicalStyleEnter
// 2. 歌曲语种审核: languageEnter
// 3. 歌曲原唱审核: oriSingerEnter
// 4. 情绪标签审核: emotionEnter

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  if (!query.examType)
    return Promise.reject({
      status: 400,
      body: {
        code: 400,
        message: '参数不足',
      },
    })
  const data = {
    examType: query.examType,
  }
  return request('/api/rep/ugc/exam/start', data, createOption(query, 'eapi'))
}
