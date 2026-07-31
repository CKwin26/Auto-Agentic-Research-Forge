import assert from "node:assert/strict";
import test from "node:test";

import {
  buildStageTwoResourcePackages,
  compactStudyLabel,
  humanizeIdentifier,
  humanizeResearchText,
  humanizeUiError,
  inferRequirement,
  phaseForTaskStage,
  preferredWorkflowSnapshot,
  taskUiState,
} from "./workflow.js";

test("a blocked task returns to the phase that owns the blocker", () => {
  assert.equal(phaseForTaskStage("stage_2_protocol"), "protocol");
  assert.equal(phaseForTaskStage("stage_3_experimentation"), "experiment");
  assert.equal(phaseForTaskStage("stage_4_synthesis"), "paper");
  assert.equal(phaseForTaskStage("unknown"), "discovery");
});

test("a completed inspection with a resolved requirement opens directions", () => {
  const task = {
    status: "succeeded",
    request: { operation: "bundle.inspect" },
    requirements: [{ requirement_id: "req-1", resolved: true }],
  };

  assert.equal(taskUiState(task), "directions");
  assert.equal(inferRequirement(task), null);
});

test("an unresolved workflow blocker is shown instead of a generic retry message", () => {
  const task = {
    status: "blocked",
    result: {
      workflow: {
        study: { current_step_ids: ["step-1"] },
        steps: [{
          step_instance_id: "step-1",
          step_type: "scope_review",
          status: "blocked",
          blocker: { message: "负责人必须批准研究范围。" },
        }],
      },
    },
  };

  const requirement = inferRequirement(task);
  assert.equal(requirement.reason, "负责人必须批准研究范围。");
  assert.equal(requirement.step_type, "scope_review");
});

test("a live study snapshot wins over the stale inspection task snapshot", () => {
  const stale = { study: { phase: "discovery" } };
  const live = { study: { phase: "protocol" } };

  assert.equal(preferredWorkflowSnapshot({
    inspection: { workflow: live },
    activeTask: { result: { workflow: stale } },
  }), live);
});

test("stage two condenses raw resources into at most three decision packages", () => {
  const resources = [
    { candidate_id: "data-1", name: "dataset.jsonl", need_type: "dataset", eligibility: "eligible" },
    { candidate_id: "tool-1", name: "runner.py", need_type: "implementation", eligibility: "conditional" },
  ];
  const topics = [
    {
      topic_id: "topic-1",
      title: "核心配对比较",
      required_resource_types: ["dataset", "implementation"],
      recommended_resource_candidate_ids: ["data-1", "tool-1"],
    },
    {
      topic_id: "topic-2",
      title: "Reproducibility and robustness",
      required_resource_types: ["dataset", "benchmark"],
      recommended_resource_candidate_ids: ["data-1"],
    },
  ];

  const packages = buildStageTwoResourcePackages({
    topics,
    resources,
    recommendedTopicId: "topic-1",
  });
  assert.equal(packages.length, 2);
  assert.equal(packages[0].name, "核心效果包");
  assert.equal(packages[0].resources.length, 2);
  assert.deepEqual(packages[1].missingTypes, ["benchmark"]);
});

test("technical research terms are projected to reader-facing copy without mutating data", () => {
  const internal = "dual_quality_top5 changes top_k_event_identification_rate";
  assert.equal(
    humanizeResearchText(internal),
    "兼顾预测质量与执行质量的候选排序方法 改变 高收益事件识别率",
  );
  assert.equal(compactStudyLabel("study-25beeb5f0ab177a7"), "研究任务 B177A7");
  assert.equal(internal, "dual_quality_top5 changes top_k_event_identification_rate");
});

test("reader-facing helpers hide unknown code identifiers and summarize machine errors", () => {
  assert.equal(
    humanizeResearchText("unknown_pipeline_key"),
    "系统内部字段",
  );
  assert.equal(
    humanizeIdentifier("resolve_experiment_profile", "系统处理步骤"),
    "系统处理步骤",
  );
  assert.equal(
    humanizeUiError(
      "generated package abstained because requirements are missing: raw contract details",
    ),
    "实验资产尚未生成。冻结契约仍缺少可执行的数据边界、对照方法、实验方法或测量规则，请返回阶段二补全后再继续。",
  );
});

test("saved English research narrative is projected into Chinese without mutating evidence", () => {
  const source = "The registered hypothesis could not be evaluated with the currently authorized resources and executable bindings.";
  assert.equal(
    humanizeResearchText(source),
    "现有授权资源和可执行条件不足以检验预先登记的研究假设。",
  );
  assert.equal(
    humanizeResearchText(
      "Where did the registered study stop, and what evidence is required before its hypothesis can be evaluated?",
    ),
    "研究在哪一环节停止，以及检验该假设前还需要哪些证据？",
  );
  assert.equal(
    humanizeResearchText(
      "The full dataset, exact runtime lock, formal baseline matrix, treatment execution, and scientific effect estimate are explicit Stage 3 tasks.",
    ),
    "完整数据集、精确运行环境、正式基线矩阵、实验组执行和科学效果估计均由阶段三完成。",
  );
  assert.equal(
    humanizeResearchText(
      "The frozen study asks whether dual_quality_top5 ranking changes top_k_event_identification_rate relative to v3_tech_quality_top5 ranking.",
    ),
    "本研究检验：与技术指标排序基线相比，兼顾预测质量与执行质量的候选排序方法是否提高高收益事件识别率。",
  );
  assert.equal(source.startsWith("The registered"), true);
});
