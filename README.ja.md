# Autonomous Project Run

![Autonomous Project Run — AIベビーシッターからの解放](assets/readme/hero-ja.png)

Autonomous Project Run（APR）は、複数のIssueがあるGitHubプロジェクトを、
曖昧な目標から検証済みの完了状態まで、少ない確認で進めるCodexスキルです。

達成したい結果を伝えると、APRが残っている作業を整理し、通常のGitHub作業と
検証を進めます。あなたに確認するのは、方針が変わる判断や安全上必要な場面です。

> **初期リリース：** `v0.5.0` はまだ安定版ではありません。まずは作業内容を
> commit済み、またはbackup済みのリポジトリで試してください。

[English](README.md)

## こんなときに役立ちます

- 関連するIssueが複数ある、または実装計画が途中で止まっている
- 1つのCodexタスクでは終わらない作業を任せたい
- テスト、レビュー、Pull Request、マージ、Issueの完了確認までを一続きで進めたい
- 「続きをやって」「これはもう終わった」と何度も伝える手間を減らしたい

## APRが行うこと

- **現在地から始めます。** リポジトリとGitHubを確認し、最初に残っている工程を
  見つけて、完了済みの作業を繰り返しません。
- **作業を混ぜません。** 実装するIssueごとにタスク、ブランチ、専用のGit作業用
  フォルダ（worktree）を分け、関係のない変更が混ざるのを防ぎます。
- **中断から戻れます。** 検証済みの進捗を保存し、プロジェクトを開き直したり、
  同じ依頼を送り直したりせず、その続きから再開します。
- **同じ操作を重ねません。** タスク作成、Pull Request、マージ、Issue完了などを
  行う前に、すでに実行済みでないか確認します。
- **工程ごとに確かめます。** 必要なテスト、コードレビュー、CI、対象commitを
  確認してから次へ進みます。
- **最後に全体を見直します。** 約束したすべてのIssueが完了しているか監査してから、
  プロジェクトの完了を報告します。

長い会話では、Codexアプリが会話の要点だけを残して整理することがあります。その場合も
APRは作業位置を保存し、残りを確認して続けます。会話が長くなったという理由だけで
仕事を放棄しません。

## インストール

APRは [`mattpocock/skills`](https://github.com/mattpocock/skills) のworkflow
skillsを利用します。先にこちらをインストールします。

```sh
npx skills@latest add mattpocock/skills
```

表示された選択肢からworkflow suiteを選び、続いてAPRをインストールします。

```sh
npx skills@latest add AkiGarage/autonomous-project-run-skill
```

すべての工程を使うには、Agent Skillsに対応したcoding app、GitHubリポジトリ、
認証済みの `gh` CLIも必要です。

APRは対象リポジトリで起動すると、必要なプロジェクト設定を確認します。Matt Pocockの
workflow設定が不足している場合は、公式setup skillを実行し、正しく設定できたことを
確かめてから作業を始めます。事前に `/setup-matt-pocock-skills` を実行する必要はありません。

## 使い方

対象リポジトリと、どこまでできたら完了なのかを伝えます。

```text
$autonomous-project-run を使って、このプロジェクトを少ない確認だけで完了まで進めてください。
```

最初から最後まで進める依頼を明確にした場合、APRはブランチ作成、テスト、レビュー、
commit、Pull Request、マージなど、その作業に必要な通常のリポジトリ操作を行えます。
APRについて質問したり、設定を確認したりするだけでは、その権限は付与されません。

## 安全のために止まる場面

APRは変更前に、対象のプロジェクトと分離されたworktreeが正しいかを確認します。
また、外部操作の結果が不明なときは状態を確認してから再試行し、タスク、Pull Request、
マージなどの重複を防ぎます。

最初から最後まで進める依頼でも、一般公開、支払い、認証情報へのアクセス、
本番環境の変更、破壊的な整理、force-push、リポジトリ保護の回避は許可されません。
これらには、それぞれ明確な許可が必要です。

別タスクへ完全に自動で引き継ぐには、Codexアプリ側がタスク作成、分離されたworktree、
安全な引き継ぎに対応している必要があります。未対応の場合でも、APRは進捗を保存し、
次に実行できる機会から続きを進めます。常駐の監視処理を勝手に追加することはありません。

## 技術資料

詳しい設計、安全ルール、状態管理、通信手順、検証項目は
[`docs/architecture/apr-lifecycle-v1/`](docs/architecture/apr-lifecycle-v1/README.md)
にまとめています。Agent向けの手順と同梱の補助スクリプトについては、配布される
[`SKILL.md`](skills/autonomous-project-run/SKILL.md) から確認できます。

## 出典とライセンス

本プロジェクトは、Wayfinderを含む
[Matt Pocock's Skills for Real Engineers](https://github.com/mattpocock/skills)
の考え方を組み合わせ、拡張しています。元プロジェクトはMIT Licenseで公開されています。
Wayfinderと組み合わせ可能なworkflow設計を公開したMatt Pocock氏に感謝します。

本リポジトリは独立して管理されており、Matt Pocock氏との提携や同氏による推奨を
示すものではありません。[LICENSE](LICENSE) と
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。

## コントリビューションとセキュリティ

検証方法とPull Requestの方針は [CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。
公開Issueに脆弱性の詳細を書かず、非公開報告または詳細を含めない連絡方法について
[SECURITY.md](SECURITY.md) に従ってください。
