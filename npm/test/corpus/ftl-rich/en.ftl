# comment
hello = Hello, { $name }!
-brand = KAERIS
about = About { -brand }
login = Log in
    .label = Login label
    .title = Login title
multi =
    first line
    second { $x }
emails = { $n ->
    [one] { $n } email
   *[other] { $n } emails
  }
photos = { $user } added { $n ->
    [one] a photo
   *[other] { $n } photos
  } to { $album }
bad = { $x
not a message
gender = { $g ->
    [male] He
    [female] She
   *[other] They
  }
