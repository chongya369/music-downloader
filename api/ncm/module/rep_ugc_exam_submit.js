// 云小编考试提交
//
// examType:
// 1. 歌曲曲风审核: musicalStyleEnter
// 2. 歌曲语种审核: languageEnter
// 3. 歌曲原唱审核: oriSingerEnter
// 4. 情绪标签审核: emotionEnter
//
// taskId:
// 首次调用 rep/ugc/exam/start 获取，之后调用 rep/ugc/exam/info/get 获取
//
// questionId:
// 调用 rep/ugc/exam/question/single/get 获取
//
// answer:
// A 对, B 错

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  if (!query.examType || !query.taskId || !query.questionId || !query.answer)
    return Promise.reject({
      status: 400,
      body: {
        code: 400,
        message: '参数不足',
      },
    })
  const data = {
    examType: query.examType,
    taskId: query.taskId,
    questionId: query.questionId,
    answer: query.answer,
  }
  return request('/api/rep/ugc/exam/submit', data, createOption(query, 'eapi'))
}
