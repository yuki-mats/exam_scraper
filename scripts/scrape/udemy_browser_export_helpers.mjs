import fs from "node:fs";
import path from "node:path";

function cleanBlock(value) {
  return String(value || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[\t \u00a0]+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

export async function extractUdemyReviewPage(tab, metadata) {
  const records = await tab.playwright.evaluate((meta) => {
    const clean = (value) =>
      String(value || "")
        .replace(/\r\n?/g, "\n")
        .split("\n")
        .map((line) => line.replace(/[\t \u00a0]+/g, " ").trim())
        .filter(Boolean)
        .join("\n");
    const imageUrls = (root) =>
      Array.from(root?.querySelectorAll("img") || [])
        .map((image) => image.currentSrc || image.src || "")
        .filter(Boolean)
        .filter((value, index, values) => values.indexOf(value) === index);

    const containers = Array.from(
      document.querySelectorAll(
        'main div[class*="result-pane--question-result-pane-wrapper"]',
      ),
    );
    return containers.map((wrapper, index) => {
      const titleSpans = Array.from(
        wrapper.querySelectorAll('span[class*="result-pane--pane-title"] span'),
      );
      const questionLabel =
        titleSpans
          .map((element) => clean(element.innerText))
          .find((text) => /^問題\d+$/.test(text)) || `問題${index + 1}`;
      const questionNumber = Number(
        (questionLabel.match(/\d+/) || [])[0] || index + 1,
      );
      const prompt = wrapper.querySelector("#question-prompt");
      const explanation = wrapper.querySelector("#overall-explanation");
      const choices = Array.from(
        wrapper.querySelectorAll('[data-purpose="answer"]'),
      ).map((answer, answerIndex) => {
        const body = answer.querySelector('[data-purpose="answer-body"]');
        const richText =
          body?.querySelector('[data-purpose^="safely-set-inner-html"]') || body;
        const correctLabel = clean(
          answer.querySelector(
            '[data-purpose="answer-result-header-user-label"]',
          )?.innerText,
        );
        return {
          number: answerIndex + 1,
          text: clean(richText?.innerText),
          html: richText?.innerHTML || "",
          is_correct:
            correctLabel === "正解" ||
            String(answer.className).includes("answer-correct"),
          image_urls: imageUrls(richText),
        };
      });
      const domainPane = wrapper.querySelector('[data-purpose="domain-pane"]');
      const domainParts = Array.from(domainPane?.children || [])
        .map((element) => clean(element.innerText))
        .filter(Boolean);
      const referenceUrls = Array.from(
        explanation?.querySelectorAll("a[href]") || [],
      )
        .map((anchor) => ({
          title: clean(anchor.innerText),
          url: anchor.href,
        }))
        .filter((item) => item.url)
        .filter(
          (item, itemIndex, items) =>
            items.findIndex((other) => other.url === item.url) === itemIndex,
        );
      const correctChoiceNumbers = choices
        .filter((choice) => choice.is_correct)
        .map((choice) => choice.number);
      return {
        course_slug: meta.courseSlug,
        quiz_id: String(meta.quizId),
        quiz_title: meta.quizTitle,
        question_number: questionNumber,
        question_label: questionLabel,
        question_text: clean(prompt?.innerText),
        question_html: prompt?.innerHTML || "",
        question_image_urls: imageUrls(prompt),
        choices,
        correct_choice_numbers: correctChoiceNumbers,
        selection_type: correctChoiceNumbers.length > 1 ? "checkbox" : "radio",
        explanation_text: clean(explanation?.innerText),
        explanation_html: explanation?.innerHTML || "",
        explanation_image_urls: imageUrls(explanation),
        domain: domainParts.length ? domainParts[domainParts.length - 1] : "",
        reference_urls: referenceUrls,
      };
    });
  }, metadata);

  validateQuizRecords(records, metadata.expectedCount || 65);
  return {
    quiz_id: String(metadata.quizId),
    quiz_title: String(metadata.quizTitle || ""),
    expected_count: Number(metadata.expectedCount || records.length),
    quiz_url: `https://tokyo-gas-dx.udemy.com/course/${encodeURIComponent(
      metadata.courseSlug,
    )}/learn/quiz/${encodeURIComponent(metadata.quizId)}/test`,
    records,
  };
}

export function validateQuizRecords(records, expectedCount) {
  if (!Array.isArray(records) || records.length !== Number(expectedCount)) {
    throw new Error(
      `Udemy問題数が一致しません: actual=${records?.length || 0} expected=${expectedCount}`,
    );
  }
  const numbers = new Set();
  for (const record of records) {
    const number = Number(record.question_number);
    if (!Number.isInteger(number) || number <= 0 || numbers.has(number)) {
      throw new Error(`Udemy問題番号が不正又は重複しています: ${number}`);
    }
    numbers.add(number);
    if (!cleanBlock(record.question_text)) {
      throw new Error(`Udemy問題文が空です: ${number}`);
    }
    if (!Array.isArray(record.choices) || record.choices.length < 2) {
      throw new Error(`Udemy選択肢が不足しています: ${number}`);
    }
    if (
      record.choices.some(
        (choice) =>
          !cleanBlock(choice.text) &&
          (!Array.isArray(choice.image_urls) || choice.image_urls.length === 0),
      )
    ) {
      throw new Error(`Udemy選択肢本文又は画像がありません: ${number}`);
    }
    if (
      !Array.isArray(record.correct_choice_numbers) ||
      record.correct_choice_numbers.length === 0
    ) {
      throw new Error(`Udemy正答がありません: ${number}`);
    }
    if (!cleanBlock(record.explanation_text)) {
      throw new Error(`Udemy解説が空です: ${number}`);
    }
  }
}

export function createUdemyBrowserExport(metadata) {
  return {
    schema_version: 1,
    source_site: "tokyo-gas-dx-udemy-com",
    course_slug: String(metadata.courseSlug || ""),
    course_title: String(metadata.courseTitle || ""),
    course_url: String(metadata.courseUrl || ""),
    expected_count: Number(metadata.expectedCount || 0),
    quizzes: [],
  };
}

export function upsertUdemyQuiz(browserExport, quiz) {
  validateQuizRecords(quiz.records, quiz.expected_count);
  const index = browserExport.quizzes.findIndex(
    (item) => String(item.quiz_id) === String(quiz.quiz_id),
  );
  if (index >= 0) browserExport.quizzes[index] = quiz;
  else browserExport.quizzes.push(quiz);
  browserExport.quizzes.sort(
    (left, right) => Number(left.quiz_id) - Number(right.quiz_id),
  );
  return browserExport;
}

export function validateUdemyBrowserExport(browserExport) {
  if (!browserExport || typeof browserExport !== "object") {
    throw new Error("Udemy browser exportはobjectである必要があります");
  }
  if (!browserExport.course_slug || !browserExport.course_url) {
    throw new Error("Udemy course情報が不足しています");
  }
  if (!Array.isArray(browserExport.quizzes) || browserExport.quizzes.length === 0) {
    throw new Error("Udemy quiz情報がありません");
  }
  const quizIds = new Set();
  let total = 0;
  for (const quiz of browserExport.quizzes) {
    const quizId = String(quiz.quiz_id || "");
    if (!quizId || quizIds.has(quizId)) {
      throw new Error(`Udemy quiz IDが不正又は重複しています: ${quizId}`);
    }
    quizIds.add(quizId);
    validateQuizRecords(quiz.records, quiz.expected_count);
    total += quiz.records.length;
  }
  if (total !== Number(browserExport.expected_count)) {
    throw new Error(
      `Udemy全問題数が一致しません: actual=${total} expected=${browserExport.expected_count}`,
    );
  }
  return browserExport;
}

export function writeUdemyBrowserExport(filePath, browserExport) {
  validateUdemyBrowserExport(browserExport);
  const directory = path.dirname(filePath);
  fs.mkdirSync(directory, { recursive: true });
  const temporaryPath = `${filePath}.tmp-${Date.now()}-${Math.random()
    .toString(16)
    .slice(2)}`;
  fs.writeFileSync(
    temporaryPath,
    `${JSON.stringify(browserExport, null, 2)}\n`,
    "utf8",
  );
  fs.renameSync(temporaryPath, filePath);
  return filePath;
}
