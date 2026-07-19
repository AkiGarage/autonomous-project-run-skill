# Autonomous Project Run

![Autonomous Project Run — AIベビーシッターからの解放](assets/readme/hero-ja.png)

曖昧な目標から始まる複数IssueのGitHubプロジェクトを、少ない監督で検証済みの完了状態まで進めるスキルです。

`autonomous-project-run` は、仕様化、依存関係付きチケット、作業を分離した実装、テスト、AIレビュー、CI、Pull Request、マージ、Issueの完了確認、最終監査までをまとめて進行します。

> 状態：pre-stable（`0.5.0`）。セキュリティとreleaseのgateは本リポジトリに記載しています。

[English](README.md)

## できること

- 計画や実装の途中からでも、最初に残っている工程を見つけて再開します。
- 方向性、範囲、取り消せない操作に関わる重要な判断だけを人に確認します。
- 1つの実装チケットを1つの新しいtaskで扱い、検証してから次へ進みます。
- compactionは永続checkpointとreplanの合図として扱い、回数だけを理由に機械的に停止しません。
- native task callを有限時間に区切り、timeout/capacity待ちを永続化し、taskを重複作成したりpolling daemonを追加したりせず、次の実host eventで再開します。
- 検証済みのsuccessorへ、crashから復旧できるhost transactionでownershipをatomicに移し、projectの開き直しや依頼の再送を求めません。
- terminal状態のownershipは、永続化した完了evidenceを確認してからtwo-phaseでreleaseし、その後のmutationをfenceしつつ、古いownershipが次の作業を止めないようにします。
- Matt Pocock氏のリポジトリ別設定が不足していないか確認し、公式setup skillを自動で呼び出して、計画や変更の前に完了を再検証します。
- recovery guardianをread-only、project-singleton、transcript非継承とし、状態に変更がない場合やterminal状態では何も出力しません。
- 正式な仕様、正確なsource state、dependencies、toolchain、生成物が一致する間だけ既存のevidenceを再利用します。
- project/worktreeの安全性、短いhandoff、Luna xhighの依頼形式を、決定的なlocal runtime gateで強制します。
- host task actionの完全一致するidentityを検証し、raw promptを保存せずに、永続request/resultのreconciliationをfail-closedにします。
- versioned lifecycle eventをproject外のatomic registryへreduceし、archiveのretry stateをworktree cleanupから分離します。
- 変更の種類ごとの追加検査を行い、全チケットのmerge後に、汚れのない正確なcommitから最終統合テストを実行します。
- production変更、credentials、支払い、破壊的操作など、明示された安全境界で停止します。
- 全チケットを横断して監査し、本当に完了しているか確認します。

## 必要なもの

- Agent Skillsに対応したcoding agent
- 全工程を使う場合は、GitHubリポジトリと認証済みの `gh` CLI
- Matt Pocock氏のworkflow skill suite（`setup-matt-pocock-skills`、`wayfinder`、`to-spec`、`to-tickets`、`implement` を含む）
- native task/thread lifecycle controls、分離されたworktree、永続化したlifecycle state、automatic successor transferに対応するhost supervisor。hostが未対応の場合はpolling daemonを追加せず、次の実host eventを待って復旧します
- hostが提供する場合はsafe-continuation handoff。ない場合は、検証済みの最小handoffを使い、正式なstateを独立して再確認できること
- Codexのレビューゲートを使う場合は `codex-autoreview`

先に元となるworkflow skillsをインストールします。

```sh
npx skills@latest add mattpocock/skills
```

表示された選択肢からworkflow suiteを選びます。関連するcompanion skillsには `grilling`、`domain-modeling`、`research`、`prototype`、`tdd`、`code-review` があります。続いて本スキルをインストールします。

```sh
npx skills@latest add AkiGarage/autonomous-project-run-skill
```

APRは対象リポジトリで起動すると、同梱のsetup preflightを実行します。必要な `docs/agents/*.md` 設定や対応する `Agent skills` の指示が不足・不完全な場合は、公式の `setup-matt-pocock-skills` skillを自動で呼び出し、そのskillが求める確認を行ったうえで、設定完了を再検証してから続行します。事前に `/setup-matt-pocock-skills` を手動実行しておく必要はありません。

入手元には公式の [`mattpocock/skills`](https://github.com/mattpocock/skills) を使ってください。管理された環境では、hostがdependency lockに対応している場合、確認済みの互換revisionに固定します。任意のGuardianにはsingleton ownership、boundedなstate-only input、transcript非継承、変更なし・terminal状態での無出力が必要です。hostがこれらを強制できない場合、本スキルはguardianを追加せず、永続化されたforeground ownerが継続します。successorのautomatic wake-upには対応hostが必要で、未対応の場合は次の検証済みhost eventで復旧します。

## 使い方

対象リポジトリと達成したい結果を指定して呼び出します。

```text
$autonomous-project-run を使って、このプロジェクトを少ない確認だけで完了まで進めてください。
```

ユーザーがend-to-endの実行を明確に依頼した場合、ブランチ作成、テスト、レビュー、commit、Pull Request、マージなどの通常作業をそのlifecycleに含めます。スキルへの言及、確認、設定だけでは、その権限は付与されません。Public公開、支払い、credentialsへのアクセス、production変更、破壊的な整理、force-push、保護ルールの回避は常に許可されません。

## リポジトリ構成

```text
skills/autonomous-project-run/
├── SKILL.md
├── agents/openai.yaml
└── scripts/
    ├── guardian_policy.py
    ├── host_actions.py
    ├── lifecycle_registry.py
    ├── runtime_gate.py
    ├── runtime_probe.py
    └── setup_preflight.py
```

lifecycleの最終目標、要件、architecture、state machine、protocol、delivery
plan、verification matrix、rollout戦略は
[`docs/architecture/apr-lifecycle-v1/`](docs/architecture/apr-lifecycle-v1/README.md)
を正本として管理します。

## 出典とライセンス

本プロジェクトは、Wayfinderを含む [Matt Pocock's Skills for Real Engineers](https://github.com/mattpocock/skills) のworkflow conceptsを組み合わせ、拡張しています。元プロジェクトはMIT Licenseで公開されています。Wayfinderと組み合わせ可能なworkflow設計を公開したMatt Pocock氏に感謝します。

本リポジトリは独立して管理されており、Matt Pocock氏との提携や同氏による推奨を示すものではありません。[LICENSE](LICENSE) と [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。

## コントリビューションとセキュリティ

検証方法とPull Requestの方針は [CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。公開Issueに脆弱性の詳細を書かず、非公開報告または詳細を含めない連絡方法について [SECURITY.md](SECURITY.md) に従ってください。
