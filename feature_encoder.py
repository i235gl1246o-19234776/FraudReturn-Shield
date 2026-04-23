import pandas as pd
import joblib
from typing import List, Dict


class OneHotFeatureEncoder:
    def __init__(self):
        self.expected_columns = []
        self.cat_cols = []
        self.fitted = False

    def fit(self, df: pd.DataFrame, categorical_cols: List[str]):
        self.cat_cols = [c for c in categorical_cols if c in df.columns]
        if self.cat_cols:
            df_cats = df[self.cat_cols].astype(str)
            dummies = pd.get_dummies(df_cats, prefix_sep='__')
            self.expected_columns = list(dummies.columns)
        self.fitted = True
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.fitted:
            raise ValueError("Encoder not fitted. Call fit() first.")

        df_out = df.drop(columns=self.cat_cols, errors='ignore').copy()

        if self.cat_cols:
            df_cats = pd.DataFrame({c: df[c].astype(str) if c in df.columns else ''
                                    for c in self.cat_cols}, index=df.index)
            dummies = pd.get_dummies(df_cats, prefix_sep='__')

            for col in self.expected_columns:
                if col not in dummies.columns:
                    dummies[col] = 0

            dummies = dummies[self.expected_columns].astype(int)
            df_out = pd.concat([df_out, dummies], axis=1)

        return df_out

    def transform_single_dict(self, features_dict: Dict) -> Dict:
        if not self.fitted:
            raise ValueError("Encoder not fitted.")

        df_single = pd.DataFrame([features_dict])
        df_encoded = self.transform(df_single)
        return df_encoded.iloc[0].to_dict()

    def save(self, path: str):
        joblib.dump(self, path)

    @staticmethod
    def load(path: str) -> 'OneHotFeatureEncoder':
        return joblib.load(path)