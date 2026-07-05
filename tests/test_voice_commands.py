import unittest

from remote_agent_protocol import voice_commands

BACKENDS = {"mock": [], "hermes": [], "hermes-yolo": [], "code-puppy": []}
ALIASES = {
    "hermes": "hermes",
    "hermes yolo": "hermes-yolo",
    "code puppy": "code-puppy",
    "puppy": "code-puppy",
    "mock": "mock",
    "the mock agent": "mock",
}


def parse(text: str):
    return voice_commands.parse_delegation(text, BACKENDS, ALIASES)


class ModelSwitchCommandTests(unittest.TestCase):
    def test_contextual_provider_switch(self):
        self.assertEqual(
            voice_commands.parse_model_switch("change to OpenAI", ALIASES),
            (None, "openai", False),
        )

    def test_explicit_agent_switch_and_retry(self):
        self.assertEqual(
            voice_commands.parse_model_switch("switch Code Puppy to OpenAI and retry", ALIASES),
            ("code-puppy", "openai", True),
        )

    def test_highest_latest_wording_is_deterministic(self):
        self.assertEqual(
            voice_commands.parse_model_switch(
                "use the highest latest OpenAI model for Hermes", ALIASES
            ),
            ("hermes", "openai", False),
        )

    def test_plain_model_discussion_is_not_a_control_command(self):
        self.assertIsNone(
            voice_commands.parse_model_switch("is the OpenAI model any good?", ALIASES)
        )

    def test_retry_follow_up(self):
        self.assertTrue(voice_commands.is_retry_request("retry that task"))
        self.assertFalse(voice_commands.is_retry_request("tell me why retries matter"))


class ParseDelegationTests(unittest.TestCase):
    # -- real utterance from ant's logs that previously got vibed at ---------
    def test_real_utterance_tell_hermes_to_write_a_file(self):
        agent, task = parse(
            "Can you tell Hermes to write a file that says test and put it on my desktop?"
        )
        self.assertEqual(agent, "hermes")
        self.assertEqual(task, "write a file that says test and put it on my desktop")

    def test_ask_hermes_to(self):
        self.assertEqual(parse("ask hermes to check my emails"), ("hermes", "check my emails"))

    def test_have_hermes_without_to(self):
        self.assertEqual(
            parse("Jess, have hermes summarize the news"),
            ("hermes", "summarize the news"),
        )

    def test_code_puppy_alias(self):
        self.assertEqual(
            parse("ask code puppy to add tests to the repo"),
            ("code-puppy", "add tests to the repo"),
        )

    def test_puppy_short_alias(self):
        self.assertEqual(
            parse("have puppy refactor the gui module"),
            ("code-puppy", "refactor the gui module"),
        )

    def test_spoken_alias_maps_to_yolo_backend(self):
        self.assertEqual(
            parse("please ask hermes yolo to clean up my downloads folder"),
            ("hermes-yolo", "clean up my downloads folder"),
        )

    # -- must NOT trigger (also straight from the logs) ----------------------
    def test_question_about_past_does_not_trigger(self):
        self.assertIsNone(parse("Did you ask Hermes to check for that?"))

    def test_status_question_does_not_trigger(self):
        self.assertIsNone(parse("Is Hermes still looking?"))

    def test_plain_chat_does_not_trigger(self):
        self.assertIsNone(parse("tell me a story about hermes the greek god"))

    def test_unknown_agent_does_not_trigger(self):
        self.assertIsNone(parse("ask gemini to write a poem"))

    def test_empty_task_does_not_trigger(self):
        self.assertIsNone(parse("ask hermes to"))


class ParseImplicitTaskTests(unittest.TestCase):
    def parse(self, text: str):
        return voice_commands.parse_implicit_task(text)

    # -- should delegate: real-world actions ---------------------------------
    def test_file_write_on_desktop(self):
        task = self.parse("Write a file that says test and put it on my desktop.")
        self.assertIsNotNone(task)
        self.assertIn("desktop", task)

    # -- real utterances from ant's 2026-07-04 session that stayed chat ------
    def test_find_closest_place_delegates(self):
        task = self.parse("Find the closest firework stands to me.")
        self.assertIsNotNone(task)
        self.assertIn("firework", task)

    def test_find_directions_delegates(self):
        task = self.parse("Find me directions to the closest fireworks stand.")
        self.assertIsNotNone(task)
        self.assertIn("directions", task)

    def test_web_search_is_standalone(self):
        self.assertIsNotNone(self.parse("Search the web for the best VR games this year"))

    def test_look_up_is_standalone(self):
        self.assertIsNotNone(self.parse("Can you look up the weather in Dallas"))

    def test_download_is_standalone(self):
        self.assertIsNotNone(self.parse("download the latest ollama installer"))

    def test_check_inbox_needs_keyword_and_has_it(self):
        self.assertIsNotNone(self.parse("please check my inbox for anything important"))

    def test_create_folder(self):
        self.assertIsNotNone(self.parse("create a folder called projects on my desktop"))

    # -- regressions: real utterances that were MISSED (gui_boot.log 18:29) --
    def test_find_trending_github_repositories(self):
        self.assertIsNotNone(
            self.parse("Find the latest trending GitHub repositories that you think are neat.")
        )

    def test_find_github_repositories_second_phrasing(self):
        self.assertIsNotNone(
            self.parse("Find the GitHub repositories for me. They're trending at least five.")
        )

    def test_plural_keyword_matches(self):
        self.assertIsNotNone(self.parse("delete the old files in my downloads"))

    def test_check_the_news(self):
        self.assertIsNotNone(self.parse("check the news for anything about AI"))

    # -- regression: gui_boot.log 18:39 -- "go and" preamble was missed ------
    def test_go_and_find_preamble(self):
        self.assertIsNotNone(self.parse("Go and find me the top 5 trending github repos."))

    def test_i_want_you_to_preamble(self):
        self.assertIsNotNone(self.parse("I want you to check my inbox for new emails"))

    def test_you_should_preamble(self):
        self.assertIsNotNone(self.parse("you should look up the weather for tomorrow"))

    # -- regression: jess_runtime.log 23:26 -- fabricated storm forecast -----
    def test_give_me_the_forecast_delegates(self):
        task = self.parse("Give me the storm forecast for Bentonville.")
        self.assertIsNotNone(task)
        self.assertIn("forecast", task)

    def test_whats_the_weather_question_still_delegates(self):
        # Live data is the one exception to the question-word guard.
        self.assertIsNotNone(self.parse("What's the weather in Bentonville?"))

    def test_hows_the_traffic_delegates(self):
        self.assertIsNotNone(self.parse("how's the traffic on the way downtown"))

    def test_tell_me_the_news_delegates(self):
        self.assertIsNotNone(self.parse("tell me the news headlines this morning"))

    def test_past_tense_weather_question_stays_with_jess(self):
        self.assertIsNone(self.parse("did you check the weather yet?"))

    def test_weather_small_talk_stays_with_jess(self):
        self.assertIsNone(self.parse("the weather is really nice today"))

    # -- should NOT delegate: chat stays with Jess ---------------------------
    def test_write_me_a_poem_stays_with_jess(self):
        self.assertIsNone(self.parse("write me a poem about space"))

    def test_make_me_laugh_stays_with_jess(self):
        self.assertIsNone(self.parse("make me laugh"))

    def test_tell_me_a_joke_stays_with_jess(self):
        self.assertIsNone(self.parse("tell me a joke"))

    def test_question_about_files_stays_with_jess(self):
        self.assertIsNone(self.parse("what is a file system?"))

    def test_past_tense_question_stays_with_jess(self):
        self.assertIsNone(self.parse("did you write the file yet?"))

    def test_is_there_a_file_question_stays_with_jess(self):
        self.assertIsNone(self.parse("is there a file on my desktop already?"))

    def test_how_do_i_question_stays_with_jess(self):
        self.assertIsNone(self.parse("how do I create a folder on my desktop?"))

    def test_browser_word_does_not_trigger_browse_verb(self):
        self.assertIsNone(self.parse("my browser is acting weird lately"))


class ParseCapabilityRequestTests(unittest.TestCase):
    def parse(self, text: str):
        return voice_commands.parse_capability_request(text)

    # -- the real utterance from jess_runtime.log 2026-07-05 12:35 that got
    # -- flattened into "Enable YouTube video watching on the computer" ------
    def test_youtube_skill_request_is_a_capability_lookup(self):
        text = (
            "there's a skill or a package that helps agents watch YouTube videos "
            "I don't remember what it's called but can you make sure the "
            "batcomputer has that"
        )
        self.assertEqual(self.parse(text), text)

    def test_verbatim_casing_is_preserved(self):
        text = "There's a Package for parsing PDFs, I forgot the name, install it"
        self.assertEqual(self.parse(text), text)

    def test_forgot_the_name_variant(self):
        self.assertIsNotNone(
            self.parse("there's a package for X but I forgot the name, can we get it")
        )

    def test_some_skill_that_does_y_variant(self):
        self.assertIsNotNone(
            self.parse(
                "there's some skill that transcribes podcasts, no idea what "
                "it's called, can you make sure we have it"
            )
        )

    def test_i_think_its_called_variant(self):
        self.assertIsNotNone(
            self.parse("that download tool, I think it's called something like wget, add it")
        )

    def test_plural_capability_noun_matches(self):
        self.assertIsNotNone(
            self.parse("there are these plugins for linting, can't remember what they're called")
        )

    # -- must NOT trigger -----------------------------------------------------
    def test_named_install_takes_the_normal_path(self):
        self.assertIsNone(self.parse("install yt-dlp on the batcomputer"))

    def test_forgetful_chat_without_capability_noun_stays_chat(self):
        self.assertIsNone(self.parse("I watched a great movie, don't remember what it's called"))

    def test_capability_noun_without_uncertainty_stays_on_normal_path(self):
        self.assertIsNone(self.parse("tell me about the requests package"))

    def test_empty_utterance_does_not_trigger(self):
        self.assertIsNone(self.parse("   "))


class RequiresConfirmationTests(unittest.TestCase):
    ELEVATED = ("yolo",)
    DESTRUCTIVE = ("delete", "remove", "format", "uninstall")

    def req(self, backend, task):
        return voice_commands.requires_confirmation(
            backend, task, elevated_markers=self.ELEVATED, destructive_words=self.DESTRUCTIVE
        )

    def test_elevated_backend_always_confirms(self):
        self.assertTrue(self.req("hermes-yolo", "say hi"))

    def test_destructive_task_confirms_on_any_backend(self):
        self.assertTrue(self.req("hermes", "delete the old files"))

    def test_plain_task_on_plain_backend_does_not_confirm(self):
        self.assertFalse(self.req("hermes", "search the web for cats"))

    def test_inflected_forms_confirm(self):
        # Substring matching catches the common imperative/past/plural forms.
        self.assertTrue(self.req("hermes", "delete my downloads"))
        self.assertTrue(self.req("hermes", "it removes the folder"))
        self.assertTrue(self.req("hermes", "uninstall that app"))

    def test_space_bounded_words_avoid_false_positives(self):
        # " rm " is space-padded in config so it only trips on the real command,
        # not on words like "storm" or "warm".
        self.assertFalse(
            voice_commands.requires_confirmation(
                "hermes", "the storm is warm", destructive_words=(" rm ",)
            )
        )


class ClassifyConfirmationReplyTests(unittest.TestCase):
    def c(self, text):
        return voice_commands.classify_confirmation_reply(text)

    def test_yes_variants_approve(self):
        for word in ("yes", "yeah", "confirm", "do it", "go ahead", "sure"):
            self.assertEqual(self.c(word), "approve", word)

    def test_no_variants_deny(self):
        for word in ("no", "cancel", "stop", "nope", "never mind", "abort"):
            self.assertEqual(self.c(word), "deny", word)

    def test_denial_wins_over_affirmation(self):
        self.assertEqual(self.c("yeah, no, cancel that"), "deny")

    def test_unrelated_reply_is_none(self):
        self.assertIsNone(self.c("what will it change exactly?"))


if __name__ == "__main__":
    unittest.main()
