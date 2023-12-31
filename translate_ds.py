import pandas as pd
import maritalk
import os
import re
import time
import signal
from dotenv import load_dotenv

load_dotenv("config.env")

KEY = os.getenv("MARITALK_KEY")
model = maritalk.MariTalk(key=KEY)

TEMPLATE = """Tarefa: Traduza o texto abaixo de forma natural para o Português do Brasil: 

{}

Lembrete: Não é necessário explicar a tradução, apenas traduza o texto de forma natural.

Tradução: """

def load_dataset(number: int) -> pd.DataFrame:
    
    # check if the dataset has already started being translated
    if f"interim_translated_ds_{number:03}.parquet" in os.listdir(
        "data/processed/interim"
    ):
        df = pd.read_parquet(f"data/processed/interim/interim_translated_ds_{number:03}.parquet")
        failure_df = pd.read_csv(f"data/processed/logs/failure_log_ds_{number:03}.csv")

    else:
        df = pd.read_parquet(f"data/raw/ds_{number:03}.parquet")
        df_messages = df.explode("messages")
        df_messages["timeOffset"] = df_messages["messages"].apply(
            lambda x: x["timeOffset"]
        )
        df_messages["text"] = df_messages["messages"].apply(lambda x: x["text"])
        df_messages["senderWorkerId"] = df_messages["messages"].apply(
            lambda x: x["senderWorkerId"]
        )
        df_messages["messageID"] = df_messages["messages"].apply(
            lambda x: x["messageId"]
        )
        df_messages = df_messages.drop("messages", axis=1).reset_index()

        df_messages["text_translated"] = None

        df = df_messages.copy()
        df["text_translated"] = None

        failure_df = pd.DataFrame(columns=["conversationId", "messageID", "text"])

    return df, failure_df


class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException

signal.signal(signal.SIGALRM, timeout_handler)

def _translate_remaining(text, row):
    global failure_df
    prompt = TEMPLATE.format(text)
    max_attempts = 2
    attempts = 0

    while attempts < max_attempts:
        try:
            # 45 seconds timeout
            # signal.alarm(45)

            answer = model.generate(
                prompt, chat_mode=False, do_sample=False, max_tokens=4096
            )

            # Success, disable the alarm
            # signal.alarm(0)

            clean_answer = answer.strip().split("\n")[0]
            time.sleep(5)

            return clean_answer

        except TimeoutException:
            attempts += 1
            print(
                f"Erro de Timeout ao traduzir a mensagem: {text}. Tentativa {attempts}."
            )
            time.sleep(5)  # Esperar 5 segundos antes de tentar novamente

        except Exception as e:
            # Disable the alarm in case of other exceptions
            # signal.alarm(0)
            
            attempts += 1
            print(
                f"Erro ao traduzir a mensagem: {text}. Tentativa {attempts}. Erro: {e}"
            )
            time.sleep(5)  # Esperar 5 segundos antes de tentar novamente

    new_row = {
        "conversationId": row["conversationId"],
        "messageID": row["messageID"],
        "text": text,
    }
    failure_df = pd.concat([failure_df, pd.DataFrame([new_row])], ignore_index=True)
    failure_df.to_csv(
        f"data/processed/logs/failure_log_{df_name[:-8]}.csv", index=False
    )

    return text

def _custom_translation(text, row, number):
    global failure_df
    text = text.strip()
    split_text = text.split()
    
    print(split_text)

    if text == "":
        return text
    
    # Caso 1 -> @123456
    #print('case 1')
    if text.startswith("@") and text[1:].isdigit() and len(split_text) == 1:
        return text

    # Caso 2 -> Or @123456
    #print('case 2')
    if len(split_text) == 2:
        if (
            split_text[0].lower() == "or"
            and split_text[1].startswith("@")
            and split_text[1][1:].isdigit()
        ):
            return "Ou " + split_text[1]

    # Caso 3 -> And @123456
    #print('case 3')
    if len(split_text) == 2:
        if (
            split_text[0].lower() == "and"
            and split_text[1].startswith("@")
            and split_text[1][1:].isdigit()
        ):
            return "E " + split_text[1]
    
    # caso 8, start with number - dataset 6
    if str(split_text[0]).isdigit():
        #print('case 8')
        return split_text[1:]

    # Caso 4 -> ! ou ? ou . ou " " ou ""
    
    elif re.match(r"^[!?. \"\"]+$", text):
        #print('case 4')
        return text

    # Caso 5 -> hello ou Hello ou HELLO ou H E L L O...
    
    elif text.lower().replace(" ", "") == "hello":
        #print('case 5')
        return "Olá"

    # Caso 6 -> @123456 is a great movie.
    
    elif text.startswith("@") and len(split_text) > 1 and split_text[0][1:].isdigit():
        print('case 6')
        text_after_number = " ".join(split_text[1:])
        translated_text_after_number = _translate_remaining(text_after_number, row, number)
        return split_text[0] + " " + translated_text_after_number
    
    # Caso 7 -> String Vazia
    elif not text:
        return text

    # Não se aplica a nenhuma das condições acima

    # caso 7, is integer
    elif str(text).isdigit():
        #print('case 7')
        return str(text)
    
    else:
        return ''


def process(df_messages: pd.DataFrame, number: int) -> pd.DataFrame:
    global failure_df
    translated_text = []
    current_message_count = 0
    success_count = 0
    failure_count = 0

    for index, row in df_messages.iterrows():
        
        success = False
        max_attempts = 2
        attempts = 0

        if row["text_translated"] != None:
            continue

        custom_translated = _custom_translation(row["text"], row)

        if custom_translated:
            translated_text.append(custom_translated)
            current_message_count += 1
            success_count += 1
            print(
                f"({current_message_count}/{total_messages}) Tradução customizada: {row['text']} -> {custom_translated}"
            )
            df_messages.loc[index, "text_translated"] = str(translated_text[-1]) # Debug
            df_messages.to_parquet(
                f"data/processed/interim/interim_translated_ds_{number:03}.parquet",
                index=False,
            )

        else:
            print('template translation')
            prompt = TEMPLATE.format(row["text"])

            while not success and attempts < max_attempts:
                print('trying to translate')
                try:
                    # 45 seconds timeout
                    # signal.alarm(45)

                    answer = model.generate(
                        prompt, chat_mode=False, do_sample=False, max_tokens=4096
                    )

                    # Disable the alarm in case of success
                    # signal.alarm(0)

                    clean_answer = answer.strip().split("\n")[0]
                    translated_text.append(clean_answer)
                    success = True
                    success_count += 1
                    current_message_count += 1
                    print(
                        f"({current_message_count}/{total_messages}) Tradução bem-sucedida: {row['text']} -> {clean_answer}"
                    )
                    time.sleep(5)

                except TimeoutException:
                    attempts += 1
                    failure_count += 1
                    current_message_count += 1
                    print(
                        f"{current_message_count}/{total_messages}) Erro de Timeout ao traduzir a mensagem: {row['text']}. Tentativa {attempts}."
                    )
                    time.sleep(5)  # Esperar 5 segundos antes de tentar novamente

                except Exception as e:
                    attempts += 1
                    print(
                        f"({current_message_count}/{total_messages}) Erro ao traduzir a mensagem: {row['text']}. Tentativa {attempts}. Erro: {e}"
                    )
                    time.sleep(5)  # Esperar 5 segundos antes de tentar novamente

            time.sleep(5)

            if not success:
                #print('failure translation')
                translated_text.append(row["text"])
                failure_count += 1
                current_message_count += 1
                new_row = {
                    "conversationId": row["conversationId"],
                    "messageID": row["messageID"],
                    "text": row["text"],
                }
                failure_df = pd.concat(
                    [failure_df, pd.DataFrame([new_row])], ignore_index=True
                )
                failure_df.to_csv(
                    f"data/processed/logs/failure_log_ds_{number:03}.csv", index=False
                )

            df_messages.loc[index, "text_translated"] = translated_text[-1]
            df_messages.to_parquet(
                f"data/processed/interim/interim_translated_ds_{number:03}.parquet",
                index=False,
            )

    return df_messages


def reaggregate_messages_and_translation(group):
    messages = group.apply(
        lambda x: {
            "timeOffset": x["timeOffset"],
            "text": x["text"],
            "senderWorkerId": x["senderWorkerId"],
            "messageId": x["messageID"],
        },
        axis=1,
    ).tolist()

    messages_translated = group.apply(
        lambda x: {
            "timeOffset": x["timeOffset"],
            "text": x["text_translated"],
            "senderWorkerId": x["senderWorkerId"],
            "messageId": x["messageID"],
        },
        axis=1,
    ).tolist()

    # Colunas constantes dentro de cada grupo
    constant_values = {
        "movieMentions": group["movieMentions"].iloc[0],
        "respondentQuestions": group["respondentQuestions"].iloc[0],
        "respondentWorkerId": group["respondentWorkerId"].iloc[0],
        "initiatorWorkerId": group["initiatorWorkerId"].iloc[0],
        "initiatorQuestions": group["initiatorQuestions"].iloc[0],
    }

    return pd.Series(
        {
            "messages": messages,
            "messages_translated": messages_translated,
            **constant_values,
        }
    )


def reconstruct_dataset(df_messages: pd.DataFrame, number: int) -> pd.DataFrame:
    reconstructed_df = (
        df_messages.groupby("conversationId")
        .apply(reaggregate_messages_and_translation)
        .reset_index()
    )
    reconstructed_df.to_parquet(
        f"data/processed/translated_ds_{number:03}.parquet", index=False
    )


if __name__ == "__main__":
    # Load dataset
    number = 4
    df = load_dataset(number=number)

    # Initial prints
    print("Mensagens originais antes da tradução:")
    print("_" * 80)
    print(df[["conversationId", "text"]].head())
    print("_" * 80)
    print("\n")

    total_messages = df["text_translated"].isna().sum()
    print('processed messages: ', df["text_translated"].notna().sum())
    print("total messages to process: ", total_messages)

    processed_df = process(df, number)
    reconstruct_dataset(processed_df, number)
